"""SEC filing shape — output of `get_company_filings`."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Filing(BaseModel):
    ticker: str
    form_type: str
    filed_at: datetime
    accession_no: str
    title: str = ""
    url: str
    risk_factors_excerpt: str | None = Field(
        default=None,
        description="First ~2k chars of Item 1A (Risk Factors) when available.",
    )
    mda_excerpt: str | None = Field(
        default=None,
        description="First ~2k chars of Item 7 (MD&A) when available.",
    )
