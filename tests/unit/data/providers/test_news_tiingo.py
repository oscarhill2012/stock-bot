"""``news/tiingo.fetch`` returns NewsArticles."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from data.models import NewsArticle


@pytest.mark.asyncio
async def test_tiingo_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tiingo JSON rows map to ``NewsArticle`` objects with the right fields."""
    import data.providers.news.tiingo as mod

    monkeypatch.setenv("TIINGO_API_KEY", "fake-key")

    payload = [
        {
            "id":            123,
            "title":         "Apple unveils Vision Pro",
            "description":   "Cupertino reveals its mixed-reality headset.",
            "url":           "https://example.test/aapl-vision-pro",
            "publishedDate": "2023-03-10T12:00:00+00:00",
            "source":        "example.test",
            "tickers":       ["aapl"],
            "tags":          ["technology"],
        },
    ]

    # Rename from `l` to `_row` to satisfy E741 (ambiguous variable name).
    monkeypatch.setattr(
        mod,
        "_fetch_news",
        lambda symbol, start, end, key, limit: payload,
    )

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(out) == 1
    assert isinstance(out[0], NewsArticle)
    assert out[0].ticker      == "AAPL"
    assert out[0].headline    == "Apple unveils Vision Pro"
    assert out[0].source      == "example.test"
    assert out[0].url         == "https://example.test/aapl-vision-pro"


def test_tiingo_registers_on_import() -> None:
    """Importing the module registers the (news, tiingo) entry."""
    import data.providers.news.tiingo  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("news", "tiingo")]
    assert entry.upstream == "tiingo"
    assert _LIMITERS["tiingo"].rate_per_minute > 0
