"""SEC filing shape — output of ``get_company_filings``."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Filing(BaseModel):
    """One SEC filing record for a single ticker.

    Covers all form types (10-K, 10-Q, 8-K, etc.).  Optional excerpt fields
    are populated selectively by the edgar provider — they are absent for form
    types that don't carry the relevant section (e.g. 8-K has no MD&A).

    Phase 7 additions (audit 2.7):
    - ``body_excerpt`` — first ~1,500 chars of the 8-K main body, populated
      by the edgar filings provider so the Fundamental LLM can classify the
      event without fetching the full document.
    - ``items_8k`` — structured list of reported Item numbers (e.g.
      ``["2.02", "9.01"]``) extracted from the 8-K header, allowing the
      extractor to filter for material events without parsing prose.
    """

    ticker: str
    form_type: str
    filed_at: datetime
    accession_no: str
    title: str = ""
    url: str = ""

    risk_factors_excerpt: str | None = Field(
        default=None,
        description="First ~2k chars of Item 1A (Risk Factors) when available.",
    )
    mda_excerpt: str | None = Field(
        default=None,
        description="First ~2k chars of Item 7 (MD&A) when available.",
    )

    # --- Phase 7 extensions (audit 2.7) — 8-K body capture ---
    # Populated by the edgar filings provider for 8-K forms only.
    body_excerpt: str | None = None             # first ~1,500 chars of main body
    items_8k: list[str] = Field(
        default_factory=list,
        description='Reported Item numbers, e.g. ["2.02", "9.01"].',
    )
