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


def test_social_sentiment_finnhub_registers_on_import() -> None:
    import data.providers.social_sentiment.finnhub  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("social_sentiment", "finnhub")]
    assert entry.upstream == "finnhub"
    # Same upstream as news/finnhub — must share the limiter singleton.
    assert _LIMITERS["finnhub"] is _LIMITERS["finnhub"]


def test_filings_edgar_registers_on_import() -> None:
    import data.providers.filings.edgar  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("filings", "edgar")]
    assert entry.upstream == "edgar"
    assert _LIMITERS["edgar"].rate_per_minute == 600
    assert _LIMITERS["edgar"].capacity == 20


def test_notable_holders_edgar_registers_on_import() -> None:
    import data.providers.notable_holders.edgar  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("notable_holders", "edgar")]
    assert entry.upstream == "edgar"
    # Same limiter singleton as filings/edgar.
    assert _LIMITERS["edgar"].rate_per_minute == 600


def test_insider_trades_edgar_registers_on_import() -> None:
    import data.providers.insider_trades.edgar  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("insider_trades", "edgar")]
    assert entry.upstream == "edgar"
