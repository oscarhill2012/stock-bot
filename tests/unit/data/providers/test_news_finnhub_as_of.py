"""Unit tests for ``data.providers.news.finnhub``.

The provider hardening (2026-05-18) introduced:

- ≤ 7-day chunking via ``_chunk_window``
- Unconditional PIT clip of ``to_date`` at ``as_of``
- URL-based de-duplication across chunks
- Truncation warning when any chunk returns ≥ 240 articles
- ``date`` / ``datetime`` polymorphic acceptance for the window kwargs

These tests pin every one of those contracts.  All network calls are
monkey-patched at the ``_fetch_company_news`` boundary so no real API key
or HTTP traffic is needed.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import pytest

# ---------------------------------------------------------------------------
# Signature / kwarg-shape tests (preserved from pre-hardening)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_accepts_as_of_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` must accept ``as_of`` and cover the requested window via chunking.

    The 15-day window splits into three ≤ 7-day chunks; verify each chunk is
    issued and that the union of chunk bounds covers ``[from_date, to_date]``.
    """
    import data.providers.news.finnhub as mod

    captured: list[dict] = []

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        """Record every chunk request without returning any articles."""
        captured.append({"symbol": symbol, "from_iso": from_iso, "to_iso": to_iso})
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert out == []

    # 15 days split into 7-day slices ⇒ 3 chunks (7 + 7 + 1).
    assert len(captured) == 3
    assert {c["symbol"] for c in captured} == {"AAPL"}

    # First chunk starts at from_date, last chunk ends at to_date.
    assert captured[0]["from_iso"]  == "2023-03-01"
    assert captured[-1]["to_iso"]   == "2023-03-15"


@pytest.mark.asyncio
async def test_fetch_accepts_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``**_unused`` absorbs kwargs other providers consume (e.g. ``lookback_days``)."""
    import data.providers.news.finnhub as mod

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: [])

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )
    assert out == []


# ---------------------------------------------------------------------------
# Chunking tests
# ---------------------------------------------------------------------------

def test_chunk_window_unit() -> None:
    """``_chunk_window`` splits a 15-day window into exactly 3 contiguous slices."""
    from data.providers.news.finnhub import _chunk_window

    chunks = _chunk_window(date(2023, 3, 1), date(2023, 3, 15), chunk_days=7)

    # 15-day inclusive window with chunk_days=7 ⇒ 7 + 7 + 1 = 3 chunks.
    assert len(chunks) == 3

    # Each chunk must be ≤ 7 days in length.
    for chunk_start, chunk_end in chunks:
        span = (chunk_end - chunk_start).days + 1
        assert span <= 7, f"Chunk span {span} exceeds 7 days"

    # Chunks must be contiguous — no gaps, no overlaps.
    from datetime import timedelta
    for i in range(len(chunks) - 1):
        _, prev_end       = chunks[i]
        next_start, _     = chunks[i + 1]
        assert next_start == prev_end + timedelta(days=1)

    # Full window must be covered.
    assert chunks[0][0]  == date(2023, 3, 1)
    assert chunks[-1][1] == date(2023, 3, 15)


@pytest.mark.asyncio
async def test_7_day_window_issues_1_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 7-day inclusive window fits in a single chunk."""
    import data.providers.news.finnhub as mod

    call_log: list[dict] = []

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        call_log.append({"from_iso": from_iso, "to_iso": to_iso})
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 6),
        to_date=date(2023, 3, 12),  # inclusive ⇒ 7 days
        as_of=datetime(2023, 3, 12, tzinfo=UTC),
    )

    assert len(call_log) == 1
    assert call_log[0]["from_iso"] == "2023-03-06"
    assert call_log[0]["to_iso"]   == "2023-03-12"


@pytest.mark.asyncio
async def test_long_window_chunked(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 40-day window splits into ⌈40 / 7⌉ = 6 chunks."""
    import data.providers.news.finnhub as mod

    call_log: list[dict] = []

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        call_log.append({"from_iso": from_iso, "to_iso": to_iso})
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    await mod.fetch(
        "AAPL",
        from_date=date(2023, 2, 27),
        to_date=date(2023, 4, 7),  # 40 days inclusive
        as_of=datetime(2023, 4, 7, tzinfo=UTC),
    )

    # ceil(40 / 7) = 6.
    assert len(call_log) == 6, f"Expected 6 chunks for 40-day window, got {len(call_log)}"

    # First chunk starts at from_date, last chunk ends at to_date.
    assert call_log[0]["from_iso"]  == "2023-02-27"
    assert call_log[-1]["to_iso"]   == "2023-04-07"


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chunking_deduplicates_by_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Articles with the same URL appearing in two chunks are de-duplicated.

    Guards the boundary-straddle case where Finnhub returns the same article
    in both the preceding and following chunk.
    """
    import data.providers.news.finnhub as mod

    shared = {
        "headline":  "Boundary article",
        "url":       "https://duplicate",
        "summary":   "Appears in two chunks",
        "source":    "Reuters",
        "datetime":  1677672000,  # 2023-03-01 12:00 UTC
    }
    unique = {
        "headline":  "Unique article",
        "url":       "https://unique",
        "summary":   "Only in chunk 2",
        "source":    "CNBC",
        "datetime":  1678276800,  # 2023-03-08 12:00 UTC
    }

    # First chunk returns the shared article; second returns shared + unique.
    chunk_responses = iter([[shared], [shared, unique]])

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        return next(chunk_responses)

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 14),  # 14 days ⇒ 2 chunks
        as_of=datetime(2023, 3, 14, tzinfo=UTC),
    )

    urls = [a.url for a in out]
    assert len(out) == 2, f"Expected 2 de-duplicated articles, got {len(out)}: {urls}"
    assert urls.count("https://duplicate") == 1
    assert "https://unique" in urls


@pytest.mark.asyncio
async def test_blank_url_articles_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Articles with an empty ``url`` are skipped — they cannot be de-duplicated.

    Otherwise a Finnhub batch with several blank-URL items would all collapse
    to a single survivor in the seen-URL set, which is misleading.
    """
    import data.providers.news.finnhub as mod

    rows = [
        {"headline": "Has URL",     "url": "https://a", "summary": "", "source": "S",
         "datetime": 1678276800},
        {"headline": "Blank URL 1", "url": "",          "summary": "", "source": "S",
         "datetime": 1678276900},
        {"headline": "Blank URL 2", "url": "",          "summary": "", "source": "S",
         "datetime": 1678277000},
    ]

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: rows)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 8),
        to_date=date(2023, 3, 8),
        as_of=datetime(2023, 3, 8, tzinfo=UTC),
    )

    assert len(out) == 1
    assert out[0].url == "https://a"


# ---------------------------------------------------------------------------
# PIT cap + window edge cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_clips_to_date_at_as_of_for_pit(monkeypatch: pytest.MonkeyPatch) -> None:
    """``to_date`` is clipped to ``as_of`` even when the caller asks for later.

    The clip is the provider's last line of defence against a sloppy caller
    leaking future articles into a backtest tick.
    """
    import data.providers.news.finnhub as mod

    call_log: list[dict] = []

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        call_log.append({"from_iso": from_iso, "to_iso": to_iso})
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    # Caller asks for window ending two weeks past as_of — must be clipped.
    await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 6),
        to_date=date(2023, 3, 26),
        as_of=datetime(2023, 3, 12, tzinfo=UTC),
    )

    # Window after clipping: [2023-03-06, 2023-03-12] = 7 days = 1 chunk.
    assert len(call_log) == 1
    assert call_log[0]["to_iso"] == "2023-03-12", (
        f"PIT cap leaked: to_iso={call_log[0]['to_iso']!r}"
    )


@pytest.mark.asyncio
async def test_response_side_pit_filter_drops_future_dated_articles(
    monkeypatch: pytest.MonkeyPatch,
    caplog:      pytest.LogCaptureFixture,
) -> None:
    """Articles dated past ``window_end`` are dropped from the response.

    Regression guard for the 2026-05-19 future-bleed diagnosis: Finnhub's
    ``/company-news`` endpoint does not strictly honour the ``to=`` upper
    bound and returns articles dated months past it.  The provider must
    post-filter every chunk by parsed publication date so the cache
    cannot receive a row past ``window_end`` no matter what the API
    hands back.

    The fake response mixes in-window and future-dated articles in the
    same chunk; only the in-window ones must survive, and a warning must
    be emitted naming the count dropped.
    """
    import data.providers.news.finnhub as mod

    # Window: [2023-03-06, 2023-03-12].  Fake response contains two
    # in-window articles and three future-dated articles that mirror the
    # production bleed pattern (dated weeks-to-months past ``to=``).
    rows = [
        {"headline": "InA",  "url": "https://a", "summary": "", "source": "S",
         "datetime": 1678147200},     # 2023-03-07 00:00 UTC — in window
        {"headline": "InB",  "url": "https://b", "summary": "", "source": "S",
         "datetime": 1678579200},     # 2023-03-12 00:00 UTC — at upper bound, in window
        {"headline": "FwA",  "url": "https://fa", "summary": "", "source": "S",
         "datetime": 1678838400},     # 2023-03-15 00:00 UTC — 3 days past window
        {"headline": "FwB",  "url": "https://fb", "summary": "", "source": "S",
         "datetime": 1681171200},     # 2023-04-11 00:00 UTC — ~1 month past
        {"headline": "FwC",  "url": "https://fc", "summary": "", "source": "S",
         "datetime": 1709251200},     # 2024-03-01 00:00 UTC — ~1 year past
    ]

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: rows)

    caplog.set_level(logging.WARNING, logger="data.providers.news.finnhub")
    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 6),
        to_date=date(2023, 3, 12),
        as_of=datetime(2023, 3, 12, tzinfo=UTC),
    )

    # Only the two in-window articles must survive (newest-first ordering).
    headlines = [a.headline for a in out]
    assert headlines == ["InB", "InA"], (
        f"PIT bleed: response-side filter let through {headlines!r}"
    )

    # The dropped-future warning must surface exactly the 3 filtered rows
    # so an audit operator notices that Finnhub returned out-of-range data.
    bleed_logs = [
        r for r in caplog.records
        if "dropped" in r.message and "future-dated" in r.message
    ]
    assert len(bleed_logs) == 1, (
        f"expected exactly one dropped-future warning, got {len(bleed_logs)}"
    )
    assert "dropped 3/5" in bleed_logs[0].message, (
        f"warning message did not name the dropped count: {bleed_logs[0].message!r}"
    )


@pytest.mark.asyncio
async def test_reversed_window_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A reversed window (``from_date > to_date``) returns ``[]`` without an API call."""
    import data.providers.news.finnhub as mod

    call_count = 0

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 20),
        to_date=date(2023, 3, 10),
        as_of=datetime(2023, 3, 25, tzinfo=UTC),
    )

    assert out == []
    assert call_count == 0


@pytest.mark.asyncio
async def test_accepts_datetime_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """``from_date`` / ``to_date`` accept ``datetime`` and coerce to ``date``."""
    import data.providers.news.finnhub as mod

    call_log: list[dict] = []

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        call_log.append({"from_iso": from_iso, "to_iso": to_iso})
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    await mod.fetch(
        "AAPL",
        from_date=datetime(2023, 3, 6, 16, 0, tzinfo=UTC),
        to_date=datetime(2023, 3, 12, 16, 0, tzinfo=UTC),
        as_of=datetime(2023, 3, 12, 20, 0, tzinfo=UTC),
    )

    assert len(call_log) == 1
    assert call_log[0]["from_iso"] == "2023-03-06"
    assert call_log[0]["to_iso"]   == "2023-03-12"


# ---------------------------------------------------------------------------
# Truncation-detection warning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_truncation_threshold_emits_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When a single chunk returns ≥ ``_TRUNCATION_WARN_THRESHOLD`` articles, log a warning.

    Reflects the empirical observation that Finnhub's free tier silently
    truncates per-call responses; a chunk near the threshold may be missing
    coverage and warrants operator attention.
    """
    import data.providers.news.finnhub as mod

    # Synthesize a chunk full of unique-URL articles right at the threshold.
    raw_rows = [
        {
            "headline":  f"Article {i}",
            "url":       f"https://example.com/{i}",
            "summary":   "",
            "source":    "Reuters",
            "datetime":  1678276800 + i,
        }
        for i in range(mod._TRUNCATION_WARN_THRESHOLD)
    ]

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: raw_rows)

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        out = await mod.fetch(
            "AAPL",
            from_date=date(2023, 3, 8),
            to_date=date(2023, 3, 8),  # single-day chunk
            as_of=datetime(2023, 3, 8, tzinfo=UTC),
            limit=None,  # keep them all so we can verify the row count
        )

    # Provider still returns the data — truncation detection is observational.
    assert len(out) == mod._TRUNCATION_WARN_THRESHOLD

    # The warning must fire and mention the threshold for operator clarity.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "truncation threshold" in r.message for r in warnings
    ), f"Expected truncation-threshold warning; got: {[r.message for r in warnings]}"


@pytest.mark.asyncio
async def test_below_truncation_threshold_no_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Normal-sized responses do not log the truncation warning."""
    import data.providers.news.finnhub as mod

    raw_rows = [
        {
            "headline":  f"Article {i}",
            "url":       f"https://example.com/{i}",
            "summary":   "",
            "source":    "Reuters",
            "datetime":  1678276800 + i,
        }
        for i in range(10)  # well below the threshold
    ]

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: raw_rows)

    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        await mod.fetch(
            "AAPL",
            from_date=date(2023, 3, 8),
            to_date=date(2023, 3, 8),
            as_of=datetime(2023, 3, 8, tzinfo=UTC),
        )

    assert not any("truncation" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Article mapping + ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_articles_sorted_newest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returned articles are sorted newest-first regardless of API order.

    All three article timestamps are placed *inside* the requested
    [2023-03-01, 2023-03-15] window so the response-side PIT filter does
    not drop them — this test is asserting sort behaviour, not the PIT
    clip.  Epochs chosen so each lands on a distinct day for legibility.
    """
    import data.providers.news.finnhub as mod

    rows = [
        # Intentionally returned oldest-first.  Epochs are mid-window UTC
        # midnight so they unambiguously sit inside [2023-03-01, 2023-03-15].
        {"headline": "Old", "url": "https://old", "summary": "", "source": "S",
         "datetime": 1677715200},     # 2023-03-02 00:00 UTC
        {"headline": "Mid", "url": "https://mid", "summary": "", "source": "S",
         "datetime": 1678233600},     # 2023-03-08 00:00 UTC
        {"headline": "New", "url": "https://new", "summary": "", "source": "S",
         "datetime": 1678752000},     # 2023-03-14 00:00 UTC
    ]

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: rows)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    headlines = [a.headline for a in out]
    assert headlines == ["New", "Mid", "Old"]


@pytest.mark.asyncio
async def test_limit_caps_returned_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """``limit=N`` caps the merged, sorted result at N articles (newest kept).

    All ten fake articles are dated 2023-03-01 (the single-day window) so
    the response-side PIT filter does not drop them — the test is about
    the cap behaviour, not the PIT clip.
    """
    import data.providers.news.finnhub as mod

    # 1677628800 = 2023-03-01 00:00 UTC; ``+ i`` shifts only by seconds so
    # every article remains on 2023-03-01 and stays within the window.
    rows = [
        {"headline": f"A{i}", "url": f"https://a/{i}", "summary": "", "source": "S",
         "datetime": 1677628800 + i}
        for i in range(10)
    ]

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: rows)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 1),
        as_of=datetime(2023, 3, 1, tzinfo=UTC),
        limit=3,
    )

    assert len(out) == 3
    # Newest-first ordering ⇒ A9, A8, A7 are the top three.
    assert [a.headline for a in out] == ["A9", "A8", "A7"]


@pytest.mark.asyncio
async def test_sentiment_always_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Finnhub free tier does not expose sentiment — every article has ``None``."""
    import data.providers.news.finnhub as mod

    rows = [
        {"headline": "Hello", "url": "https://a", "summary": "World", "source": "Reuters",
         "datetime": 1678276800},
    ]
    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: rows)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 8),
        to_date=date(2023, 3, 8),
        as_of=datetime(2023, 3, 8, tzinfo=UTC),
    )

    assert len(out) == 1
    assert out[0].sentiment is None


@pytest.mark.asyncio
async def test_ticker_uppercased(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lower-case ticker input is normalised before the API call and on the result."""
    import data.providers.news.finnhub as mod

    captured: dict = {}

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        captured["symbol"] = symbol
        return [
            {"headline": "x", "url": "https://x", "summary": "", "source": "S",
             "datetime": 1678276800},
        ]

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    out = await mod.fetch(
        "aapl",
        from_date=date(2023, 3, 8),
        to_date=date(2023, 3, 8),
        as_of=datetime(2023, 3, 8, tzinfo=UTC),
    )

    assert captured["symbol"] == "AAPL"
    assert out[0].ticker      == "AAPL"
