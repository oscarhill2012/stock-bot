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

    Wraps a pre-built ``MagicMock`` response so that
    ``async with httpx.AsyncClient(...) as client`` resolves to an object
    whose ``get()`` coroutine returns the stub.

    Parameters
    ----------
    resp:
        The ``MagicMock`` that represents the HTTP response.
    """

    def __init__(self, resp: MagicMock) -> None:
        self._resp = resp

    async def __aenter__(self) -> _AsyncCM:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def get(self, *args, **kwargs) -> MagicMock:
        """Simulate ``AsyncClient.get(...)`` returning the stub response."""
        return self._resp


def _make_fake_client(payload: dict) -> MagicMock:
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
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
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
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
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
        lambda *a, **k: _AsyncCM(_make_fake_client({"feed": []})),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert out == []


@pytest.mark.asyncio
async def test_alpha_vantage_missing_feed_key_returns_empty_list(monkeypatch):
    """A payload without a ``feed`` key (e.g. error response) returns ``[]``."""
    from data.providers.news import alpha_vantage as mod

    monkeypatch.setattr(mod, "require_key", lambda _: "test-token")
    monkeypatch.setattr(
        mod.httpx, "AsyncClient",
        lambda *a, **k: _AsyncCM(_make_fake_client({"Information": "rate limit hit"})),
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
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
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
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
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
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
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
        lambda *a, **k: _AsyncCM(_make_fake_client(payload)),
    )

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12), lookback_days=7)

    assert len(out) == 1
    assert out[0].sentiment is None


# ---------------------------------------------------------------------------
# Integration / slow test (real network — skipped in CI)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.asyncio
async def test_alpha_vantage_live_fetch():
    """Integration smoke-test against the real AV endpoint.

    Requires ``ALPHA_VANTAGE_API_KEY`` to be set in the environment (or
    ``.env``).  Marked ``@pytest.mark.slow`` — excluded from the default
    ``pytest`` run.

    Verifies that:
    - At least one ``NewsArticle`` is returned for a liquid ticker.
    - Each article has a non-empty headline.
    - ``sentiment`` is a float (AV always provides this for ``NEWS_SENTIMENT``).
    """
    from data.providers.news import alpha_vantage as mod

    # Use a well-covered date from the SVB stress window.
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15), lookback_days=7)

    # We can't assert a specific count, but the AV archive should have at
    # least one article for AAPL in any 7-day window during March 2023.
    assert len(out) > 0, "Expected at least one article for AAPL in SVB window"

    for article in out:
        assert article.headline, f"Empty headline for article: {article}"
        assert article.sentiment is not None, (
            f"Expected sentiment for AV article: {article.headline!r}"
        )
