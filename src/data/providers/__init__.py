"""Per-source provider modules. Importing each module triggers its @register call."""
from . import company_ratios as _company_ratios  # noqa: F401
from .analyst_consensus import yfinance as _analyst_consensus_yfinance  # noqa: F401  — Task 3.6
from .earnings import finnhub as _earnings_finnhub  # noqa: F401  — Task 3.1
from .filings import edgar as _filings_edgar  # noqa: F401
from .insider_trades import edgar as _insider_trades_edgar  # noqa: F401
from .news import alpha_vantage as _news_alpha_vantage  # noqa: F401  — Task 3.2
from .news import finnhub as _news_finnhub  # noqa: F401
from .notable_holders import edgar as _notable_holders_edgar  # noqa: F401
from .politician_trades import quiver as _politician_trades_quiver  # noqa: F401
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

__all__: list[str] = []
