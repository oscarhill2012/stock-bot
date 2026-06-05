"""Unit tests for ``data.providers.news.alpha_vantage``.

All HTTP calls are monkeypatched — no real network traffic or API key is
needed for these tests.

The ``_AsyncCM`` helper replicates the pattern used in
``test_earnings_finnhub_as_of.py``; if it appears in a third test file it
should be hoisted into ``tests/unit/data/providers/conftest.py``.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Async context-manager shim for httpx.AsyncClient
# ---------------------------------------------------------------------------

class _AsyncCM:
    """Minimal async context-manager that yields a stub httpx response.

    Wraps a pre-built ``MagicMock`` response (or a list of responses for
    multi-call scenarios) so that ``async with httpx.AsyncClient(...) as client``
    resolves to an object whose ``get()`` coroutine returns the stub(s).

    Parameters
    ----------
    resp:
        Either a single ``MagicMock`` response (returned for every ``get``
        call), or a list of ``MagicMock`` responses returned in order
        (one per call).  If a list is exhausted, the last item is repeated.
    """

    def __init__(self, resp: MagicMock | list[MagicMock]) -> None:
        if isinstance(resp, list):
            self._resps = resp
        else:
            self._resps = [resp]
        self._call_index = 0

    async def __aenter__(self) -> _AsyncCM:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, *args, **kwargs) -> MagicMock:
        """Simulate ``AsyncClient.get(...)`` returning the next stub response."""
        idx = min(self._call_index, len(self._resps) - 1)
        self._call_index += 1
        return self._resps[idx]


def _make_fake_resp(payload: dict) -> MagicMock:
    """Return a MagicMock response whose ``.json()`` yields ``payload``."""
    fake_resp = MagicMock()
    fake_resp.json.return_value = payload
    fake_resp.raise_for_status = lambda: None
    return fake_resp


# ---------------------------------------------------------------------------
# Happy-path tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alpha_vantage_populates_sentiment_and_relevance(monkeypatch):
    """Core contract: ``sentiment`` and ``relevance`` are populated correctly.

    This is the canonical test from the spec (Task 3.2, Step 1).  It verifies
    that:

    - ``sentiment`` is taken from ``overall_sentiment_score``
    - ``relevance`` is taken from the matching ticker's ``relevance_score``
      inside ``ticker_sentiment[]``
    - Only the requesting ticker's relevance is extracted (not MSFT's)
    """
    from data.providers.news import alpha_vantage as mod

    payload = {"feed": [{
        "title": "Apple beats",
        "url": "https://x",
        "summary": "...",
        "time_published": "20230310T120000",
        "source": "Reuters",
        "overall_sentiment_score": 0.45,
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.87",
             "ticker_sentiment_score": "0.51"},
            {"ticker": "MSFT", "relevance_score": "0.21",
             "ticker_sentiment_score": "0.30"},
        ],
    }]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(out) == 1
    assert out[0].sentiment == 0.45
    assert abs(out[0].relevance - 0.87) < 1e-6


@pytest.mark.asyncio
async def test_alpha_vantage_returns_correct_article_fields(monkeypatch):
    """All basic ``NewsArticle`` fields are mapped from the AV feed row."""
    from data.providers.news import alpha_vantage as mod

    payload = {"feed": [{
        "title": "Apple beats estimates",
        "url": "https://example.com/article",
        "summary": "Apple reported strong quarterly earnings.",
        "time_published": "20230310T143000",
        "source": "Reuters",
        "overall_sentiment_score": 0.30,
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.92",
             "ticker_sentiment_score": "0.45"},
        ],
    }]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(out) == 1
    article = out[0]
    assert article.ticker == "AAPL"
    assert article.headline == "Apple beats estimates"
    assert article.url == "https://example.com/article"
    assert article.source == "Reuters"
    assert article.published_at.year == 2023
    assert article.published_at.month == 3
    assert article.published_at.day == 10


@pytest.mark.asyncio
async def test_alpha_vantage_empty_feed_returns_empty_list(monkeypatch):
    """An empty ``feed`` list produces an empty result without raising."""
    from data.providers.news import alpha_vantage as mod

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp({"feed": []})),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert out == []


@pytest.mark.asyncio
async def test_alpha_vantage_information_envelope_raises(monkeypatch):
    """An ``Information`` envelope (rate-limit / quota) must raise.

    AV's free-tier rate-limit response arrives as HTTP 200 with
    ``{"Information": "...25 requests/day..."}`` and no ``feed`` key — which
    is indistinguishable from a genuinely empty feed unless the provider
    explicitly checks for the envelope.  We raise so the cache_runs layer
    surfaces the outage instead of writing ``status=ok, rows_written=0``.
    """
    from data.providers.news import alpha_vantage as mod

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp({"Information": "rate limit hit"})),
    )

    with pytest.raises(mod.AlphaVantageEnvelopeError, match="Information"):
        await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)


@pytest.mark.asyncio
async def test_alpha_vantage_note_envelope_raises(monkeypatch):
    """A ``Note`` envelope (legacy throttle notification) must also raise."""
    from data.providers.news import alpha_vantage as mod

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp({"Note": "throttled"})),
    )

    with pytest.raises(mod.AlphaVantageEnvelopeError, match="Note"):
        await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)


@pytest.mark.asyncio
async def test_alpha_vantage_error_message_envelope_raises(monkeypatch):
    """An ``Error Message`` envelope (e.g. invalid params) must raise."""
    from data.providers.news import alpha_vantage as mod

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(
            _make_fake_resp({"Error Message": "Invalid API call"})
        ),
    )

    with pytest.raises(mod.AlphaVantageEnvelopeError, match="Error Message"):
        await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)


@pytest.mark.asyncio
async def test_alpha_vantage_missing_feed_key_without_envelope_returns_empty(monkeypatch):
    """A payload with neither ``feed`` nor an envelope key still returns ``[]``.

    Preserves graceful behaviour for AV responses that happen to omit
    ``feed`` without signalling an error — we only raise when AV
    *explicitly* indicates a non-data state via one of the envelope keys.
    """
    from data.providers.news import alpha_vantage as mod

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp({})),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert out == []


@pytest.mark.asyncio
async def test_alpha_vantage_ticker_absent_from_sentiment_list(monkeypatch):
    """Relevance is ``None`` when the requested ticker is absent from ``ticker_sentiment``.

    AV sometimes omits a ticker from ``ticker_sentiment`` even though the
    article is tagged to it.  The provider must not raise — it should leave
    ``relevance`` as ``None``.
    """
    from data.providers.news import alpha_vantage as mod

    payload = {"feed": [{
        "title": "Market news",
        "url": "https://example.com/market",
        "summary": "General market update.",
        "time_published": "20230310T090000",
        "source": "Bloomberg",
        "overall_sentiment_score": -0.10,
        # No AAPL entry in ticker_sentiment.
        "ticker_sentiment": [
            {"ticker": "SPY", "relevance_score": "0.50",
             "ticker_sentiment_score": "-0.05"},
        ],
    }]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(out) == 1
    assert out[0].relevance is None
    assert out[0].sentiment == pytest.approx(-0.10)


@pytest.mark.asyncio
async def test_alpha_vantage_multiple_articles_returned(monkeypatch):
    """Multiple feed rows produce multiple ``NewsArticle`` objects."""
    from data.providers.news import alpha_vantage as mod

    payload = {"feed": [
        {
            "title": "Article A", "url": "https://a", "summary": "A",
            "time_published": "20230309T080000", "source": "Reuters",
            "overall_sentiment_score": 0.20,
            "ticker_sentiment": [
                {"ticker": "AAPL", "relevance_score": "0.60",
                 "ticker_sentiment_score": "0.20"},
            ],
        },
        {
            "title": "Article B", "url": "https://b", "summary": "B",
            "time_published": "20230308T160000", "source": "CNBC",
            "overall_sentiment_score": -0.15,
            "ticker_sentiment": [
                {"ticker": "AAPL", "relevance_score": "0.45",
                 "ticker_sentiment_score": "-0.15"},
            ],
        },
    ]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(out) == 2
    assert out[0].headline == "Article A"
    assert out[1].headline == "Article B"


@pytest.mark.asyncio
async def test_alpha_vantage_ticker_uppercased(monkeypatch):
    """The ticker on each returned article is upper-cased regardless of input case."""
    from data.providers.news import alpha_vantage as mod

    payload = {"feed": [{
        "title": "Apple news", "url": "https://x", "summary": "",
        "time_published": "20230310T120000", "source": "AP",
        "overall_sentiment_score": 0.05,
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.70",
             "ticker_sentiment_score": "0.05"},
        ],
    }]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp(payload)),
    )

    # Pass lower-case ticker to verify normalisation.
    out = await mod.fetch("aapl", as_of=date(2023, 3, 12), lookback_days=7)

    assert out[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_alpha_vantage_missing_sentiment_score_is_none(monkeypatch):
    """``sentiment`` is ``None`` when ``overall_sentiment_score`` is absent from the row."""
    from data.providers.news import alpha_vantage as mod

    payload = {"feed": [{
        "title": "Brief headline", "url": "https://y", "summary": "",
        "time_published": "20230310T100000", "source": "Seeking Alpha",
        # overall_sentiment_score intentionally omitted.
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.55",
             "ticker_sentiment_score": "0.10"},
        ],
    }]}

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_resp(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(out) == 1
    assert out[0].sentiment is None


# ---------------------------------------------------------------------------
# Monthly chunking tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alpha_vantage_chunk_window_unit():
    """``_chunk_window`` splits a 75-day window into exactly 3 slices of ≤ 30 days."""
    from data.providers.news.alpha_vantage import _chunk_window

    start = date(2023, 1, 1)
    end = date(2023, 3, 16)   # 75 days inclusive (Jan 31 + Feb 28 + 16 = 75)
    chunks = _chunk_window(start, end, chunk_days=30)

    assert len(chunks) == 3

    # Each chunk must be ≤ 30 days in length.
    for chunk_start, chunk_end in chunks:
        span = (chunk_end - chunk_start).days + 1
        assert span <= 30, f"Chunk span {span} exceeds 30 days"

    # Chunks must be contiguous — no gaps, no overlaps.
    for i in range(len(chunks) - 1):
        _, prev_end = chunks[i]
        next_start, _ = chunks[i + 1]
        assert next_start == prev_end + __import__("datetime").timedelta(days=1)

    # Full window must be covered.
    assert chunks[0][0] == start
    assert chunks[-1][1] == end


@pytest.mark.asyncio
async def test_alpha_vantage_75_day_window_issues_3_calls(monkeypatch):
    """A 75-day ``lookback_days`` window results in exactly 3 AV API calls.

    Each chunk produces one ``client.get()`` invocation.  We assert the call
    count by recording every ``get`` call via a tracking list.
    """
    from data.providers.news import alpha_vantage as mod

    # Return an empty feed for every chunk — we care only about call count.
    empty_payload = {"feed": []}

    call_log: list[dict] = []

    class _TrackingCM:
        """Records every ``get`` call and returns an empty-feed response."""

        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str, params: dict | None = None, **kwargs) -> MagicMock:
            """Log the call and return a stub empty-feed response."""
            call_log.append({"url": url, "params": params or {}})
            return _make_fake_resp(empty_payload)

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _TrackingCM(),
    )

    # 75 days → ceil(75 / 30) = 3 chunks.
    await mod.fetch("AAPL", as_of=date(2023, 3, 16), lookback_days=75)

    assert len(call_log) == 3, (
        f"Expected 3 AV API calls for a 75-day window, got {len(call_log)}"
    )


@pytest.mark.asyncio
async def test_alpha_vantage_30_day_window_issues_1_call(monkeypatch):
    """A window of exactly 30 days results in a single AV API call (no splitting needed).

    ``lookback_days=29`` produces a 30-day window (``[as_of - 29, as_of]``
    inclusive), which fits exactly in one ≤ 30-day chunk.  Note that
    ``lookback_days=30`` yields a 31-day window and would produce two chunks.
    """
    from data.providers.news import alpha_vantage as mod

    call_log: list = []

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, *args, **kwargs) -> MagicMock:
            call_log.append(True)
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    # lookback_days=29 → window [as_of-29, as_of] = 30 days inclusive → 1 chunk.
    await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=29)

    assert len(call_log) == 1


@pytest.mark.asyncio
async def test_alpha_vantage_chunking_deduplicates_by_url(monkeypatch):
    """Articles with the same URL appearing in two chunks are de-duplicated.

    This guards against boundary-straddle duplicates if AV returns articles
    near a chunk boundary in both the preceding and following chunk.
    """
    from data.providers.news import alpha_vantage as mod

    shared_article = {
        "title": "Boundary article",
        "url": "https://duplicate",
        "summary": "Appears in two chunks",
        "time_published": "20230130T120000",
        "source": "Reuters",
        "overall_sentiment_score": 0.10,
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.50",
             "ticker_sentiment_score": "0.10"},
        ],
    }
    unique_article = {
        "title": "Unique article",
        "url": "https://unique",
        "summary": "Only in chunk 2",
        "time_published": "20230215T080000",
        "source": "CNBC",
        "overall_sentiment_score": 0.20,
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.60",
             "ticker_sentiment_score": "0.20"},
        ],
    }

    # Chunk 1 returns the shared article; chunk 2 returns both (duplicate + unique).
    chunk_responses = [
        _make_fake_resp({"feed": [shared_article]}),
        _make_fake_resp({"feed": [shared_article, unique_article]}),
    ]

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(chunk_responses),
    )

    # Use exactly 60 days to guarantee 2 chunks.
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 1), lookback_days=60)

    urls = [a.url for a in out]
    assert len(out) == 2, f"Expected 2 de-duplicated articles, got {len(out)}: {urls}"
    assert urls.count("https://duplicate") == 1
    assert "https://unique" in urls


@pytest.mark.asyncio
async def test_alpha_vantage_timestamp_format_uses_hhmmss(monkeypatch):
    """``time_to`` in AV params ends with ``T235959``, not ``T2359``.

    This is the M4 fix — the old format ``%Y%m%dT2359`` produced a
    malformed literal; the correct format ``%Y%m%dT235959`` matches the AV
    documented format and the ``_parse_ts`` format string.
    """
    from data.providers.news import alpha_vantage as mod

    call_params: list[dict] = []

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str, params: dict | None = None, **kwargs) -> MagicMock:
            call_params.append(params or {})
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(call_params) == 1
    time_to = call_params[0].get("time_to", "")
    assert time_to.endswith("T235959"), (
        f"Expected time_to to end with T235959, got: {time_to!r}"
    )


# ---------------------------------------------------------------------------
# Window resolution — ``from_date`` / ``to_date`` kwargs (PIT-aware)
# ---------------------------------------------------------------------------
#
# These tests pin the contract between ``data.get_stock_news`` (which forwards
# ``from_date`` / ``to_date``) and the AV provider.  Before this contract was
# enforced, the provider silently ignored both kwargs and used the default
# ``lookback_days=7`` — meaning a backtest fetcher asking for a 40-day SVB
# window only ever got the last 7 days of news.

@pytest.mark.asyncio
async def test_alpha_vantage_honours_from_date_to_date(monkeypatch):
    """When ``from_date`` is supplied the window starts there, not at ``as_of - lookback_days``.

    Regression guard: the backtest fetcher calls into AV with a 40-day span
    via ``from_date`` / ``to_date``.  The provider must chunk that whole
    span, not the default 7-day window.
    """
    from data.providers.news import alpha_vantage as mod

    call_params: list[dict] = []

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str, params: dict | None = None, **kwargs) -> MagicMock:
            call_params.append(params or {})
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    # 40 days = 2 chunks under the 30-day cap.
    await mod.fetch(
        "AAPL",
        as_of=date(2023, 4, 7),
        from_date=date(2023, 2, 27),
        to_date=date(2023, 4, 7),
        # `lookback_days` would imply a 7-day window if used — verifies it is ignored.
        lookback_days=7,
    )

    assert len(call_params) == 2, (
        f"Expected 2 chunks for a 40-day window, got {len(call_params)}"
    )
    assert call_params[0]["time_from"].startswith("20230227")
    assert call_params[-1]["time_to"].startswith("20230407")


@pytest.mark.asyncio
async def test_alpha_vantage_falls_back_to_lookback_days_when_no_window_kwargs(monkeypatch):
    """Without ``from_date``/``to_date`` the legacy ``lookback_days`` path is preserved."""
    from data.providers.news import alpha_vantage as mod

    call_params: list[dict] = []

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str, params: dict | None = None, **kwargs) -> MagicMock:
            call_params.append(params or {})
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    # lookback=7 fits in a single chunk; window starts 7 days before as_of.
    assert len(call_params) == 1
    assert call_params[0]["time_from"].startswith("20230305")
    assert call_params[0]["time_to"].startswith("20230312")


@pytest.mark.asyncio
async def test_alpha_vantage_clips_to_date_at_as_of_for_pit(monkeypatch):
    """``to_date`` is clipped to ``as_of`` even when the caller asks for later — PIT cap.

    The cap is the provider's last line of defence against a sloppy caller
    leaking future articles into a backtest tick.
    """
    from data.providers.news import alpha_vantage as mod

    call_params: list[dict] = []

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str, params: dict | None = None, **kwargs) -> MagicMock:
            call_params.append(params or {})
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    # Caller asks for window ending two weeks past as_of — must be clipped.
    await mod.fetch(
        "AAPL",
        as_of=date(2023, 3, 12),
        from_date=date(2023, 3, 5),
        to_date=date(2023, 3, 26),
    )

    assert len(call_params) == 1
    assert call_params[0]["time_to"].startswith("20230312"), (
        f"PIT cap leaked: time_to={call_params[0]['time_to']!r}"
    )


@pytest.mark.asyncio
async def test_alpha_vantage_accepts_datetime_for_from_date(monkeypatch):
    """``from_date`` accepts ``datetime`` and coerces to ``date`` correctly.

    The live pipeline forwards tz-aware datetimes; ``_coerce_date`` handles
    the cross-type case so the provider doesn't reject them.
    """
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    from data.providers.news import alpha_vantage as mod

    call_params: list[dict] = []

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, url: str, params: dict | None = None, **kwargs) -> MagicMock:
            call_params.append(params or {})
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    await mod.fetch(
        "AAPL",
        as_of=date(2023, 3, 12),
        from_date=_dt(2023, 3, 5, 16, 0, tzinfo=_UTC),
        to_date=_dt(2023, 3, 12, 16, 0, tzinfo=_UTC),
    )

    assert len(call_params) == 1
    assert call_params[0]["time_from"].startswith("20230305")


@pytest.mark.asyncio
async def test_alpha_vantage_reversed_window_raises(monkeypatch):
    """A reversed window (``from_date > to_date``) raises ``ValueError`` without an API call.

    The previous behaviour was to silently return ``[]``, which masked backtest
    mis-windowing — an inexplicably empty newsfeed was indistinguishable from a
    genuine zero-article window.  Raising means the offending bounds surface
    immediately in the caller's stack trace.
    """
    from data.providers.news import alpha_vantage as mod

    call_count = 0

    class _TrackingCM:
        async def __aenter__(self) -> _TrackingCM:
            return self

        async def __aexit__(self, *exc) -> bool:
            return False

        async def get(self, *args, **kwargs) -> MagicMock:
            nonlocal call_count
            call_count += 1
            return _make_fake_resp({"feed": []})

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _TrackingCM())

    with pytest.raises(ValueError, match="reversed news window"):
        await mod.fetch(
            "AAPL",
            as_of=date(2023, 3, 12),
            from_date=date(2023, 3, 20),
            to_date=date(2023, 3, 10),
        )

    # The raise must happen before any API call — no request should escape.
    assert call_count == 0
