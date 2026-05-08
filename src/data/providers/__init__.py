"""Per-source provider modules. Importing each module triggers its @register call."""
from .filings import edgar as _filings_edgar  # noqa: F401
from .news import finnhub as _news_finnhub  # noqa: F401
from .notable_holders import edgar as _notable_holders_edgar  # noqa: F401
from .quiver_politicians import get_public_figure_trades  # noqa: E402
from .sec_insiders import get_insider_trades  # noqa: E402
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

__all__ = [
    "get_public_figure_trades",
    "get_insider_trades",
]
