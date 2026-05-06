"""SEC filing shape — output of `get_company_filings`."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Filing(BaseModel):
    ticker: str
    form_type: str
    filed_at: datetime
    accession_no: str
    title: str = ""
    url: str
    risk_factors_excerpt: Optional[str] = Field(
        default=None,
        description="First ~2k chars of Item 1A (Risk Factors) when available.",
    )
    mda_excerpt: Optional[str] = Field(
        default=None,
        description="First ~2k chars of Item 7 (MD&A) when available.",
    )
