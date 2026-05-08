"""Per-source provider modules. Importing each module triggers its @register call."""
# Below imports remain unchanged for now (subsequent tasks migrate them):
from .finnhub_news import get_stock_news
from .finnhub_social import get_social_sentiment
from .quiver_politicians import get_public_figure_trades
from .sec_filings import get_company_filings
from .sec_holders import get_notable_holders
from .sec_insiders import get_insider_trades
from .stats import yfinance as _stats_yfinance  # noqa: F401

__all__ = [
    "get_stock_news",
    "get_social_sentiment",
    "get_public_figure_trades",
    "get_company_filings",
    "get_insider_trades",
    "get_notable_holders",
    # `get_stock_stats` removed from this list — superseded by the registry path.
]
