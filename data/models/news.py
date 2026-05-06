"""News article model — output of `get_stock_news`."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NewsArticle(BaseModel):
    ticker: str
    headline: str
    summary: str = ""
    url: str
    source: str = ""
    published_at: datetime
    sentiment: Optional[float] = Field(
        default=None,
        description="Per-article sentiment in [-1.0, 1.0] when supplied by provider.",
    )
