"""Smoke tests: each provider module registers itself when imported."""
from __future__ import annotations


def test_stats_yfinance_registers_on_import() -> None:
    # Importing the provider module triggers its @register decorator.
    import data.providers.stats.yfinance  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("stats", "yfinance")]
    assert entry.upstream == "yfinance"


def test_news_finnhub_registers_on_import() -> None:
    import data.providers.news.finnhub  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("news", "finnhub")]
    assert entry.upstream == "finnhub"
