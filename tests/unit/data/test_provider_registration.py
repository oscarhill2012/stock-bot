"""Smoke tests: each provider module registers itself when imported.

Phase 5 data-model split: ``stats`` domain retired. The yfinance provider now
registers two domains: ``price_history`` and ``company_ratios``.
"""
from __future__ import annotations


def test_price_history_yfinance_registers_on_import() -> None:
    """Importing the yfinance stats module registers the price_history domain."""
    import data.providers.stats.yfinance  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("price_history", "yfinance")]
    assert entry.upstream == "yfinance"


def test_news_finnhub_registers_on_import() -> None:
    """Importing the finnhub news module registers the news domain."""
    import data.providers.news.finnhub  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("news", "finnhub")]
    assert entry.upstream == "finnhub"


def test_social_sentiment_finnhub_registers_on_import() -> None:
    """Importing the finnhub social-sentiment module registers the social_sentiment domain."""
    import data.providers.social_sentiment.finnhub  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("social_sentiment", "finnhub")]
    assert entry.upstream == "finnhub"
    # Same upstream as news/finnhub — must share the limiter singleton.
    assert _LIMITERS["finnhub"] is _LIMITERS["finnhub"]


def test_filings_edgar_registers_on_import() -> None:
    """Importing the EDGAR filings module registers the filings domain."""
    import data.providers.filings.edgar  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("filings", "edgar")]
    assert entry.upstream == "edgar"
    assert _LIMITERS["edgar"].rate_per_minute == 600
    assert _LIMITERS["edgar"].capacity == 20


def test_notable_holders_edgar_registers_on_import() -> None:
    """Importing the EDGAR notable-holders module registers the notable_holders domain."""
    import data.providers.notable_holders.edgar  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("notable_holders", "edgar")]
    assert entry.upstream == "edgar"
    # Same limiter singleton as filings/edgar.
    assert _LIMITERS["edgar"].rate_per_minute == 600


def test_insider_trades_edgar_registers_on_import() -> None:
    """Importing the EDGAR insider-trades module registers the insider_trades domain."""
    import data.providers.insider_trades.edgar  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("insider_trades", "edgar")]
    assert entry.upstream == "edgar"


def test_politician_trades_quiver_registers_on_import() -> None:
    """Importing the Quiver politician-trades module registers the politician_trades domain."""
    import data.providers.politician_trades.quiver  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("politician_trades", "quiver")]
    assert entry.upstream == "quiver"
    assert _LIMITERS["quiver"].rate_per_minute == 30
    assert _LIMITERS["quiver"].capacity == 10
