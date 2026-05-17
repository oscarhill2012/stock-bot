"""News article model — output of ``get_stock_news``."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    """One news article associated with a single ticker.

    ``sentiment`` is populated by providers that perform per-article NLP
    (e.g. Alpha Vantage, Finnhub).  ``relevance`` is an Alpha Vantage
    per-ticker per-article score in [0.0, 1.0]; other providers leave it
    ``None``.
    """

    ticker: str
    headline: str
    summary: str = ""
    url: str
    source: str = ""
    published_at: datetime

    sentiment: float | None = Field(
        default=None,
        description="Per-article sentiment in [-1.0, 1.0] when supplied by provider.",
    )

    # Phase 7 extension (audit row — Alpha Vantage per-ticker relevance score).
    # Other news providers do not supply this; they leave it None.
    relevance: float | None = None   # [0.0, 1.0]; None for non-Alpha-Vantage sources
