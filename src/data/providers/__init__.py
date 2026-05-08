"""Per-source provider modules. Importing each module triggers its @register call."""
from .news import finnhub as _news_finnhub  # noqa: F401
from .quiver_politicians import get_public_figure_trades
from .sec_filings import get_company_filings
from .sec_holders import get_notable_holders
from .sec_insiders import get_insider_trades
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

__all__ = [
    "get_public_figure_trades",
    "get_company_filings",
    "get_insider_trades",
    "get_notable_holders",
]
