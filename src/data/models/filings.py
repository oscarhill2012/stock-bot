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

    Phase 13 additions (remove-filing-boilerplate):
    - ``period_of_report`` — the conformed period of report from the SEC SGML
      header (YYYYMMDD string, e.g. ``"20240930"``).  Used to pair current
      periodic filings with their prior-year counterparts so the assembly
      layer can de-boilerplate MD&A text by diffing out unchanged paragraphs.
      Available from edgartools ``Filing.period_of_report`` at no extra
      network cost (extracted from SGML header metadata).

    Phase 14 additions (filing-delta / Lazy Prices):
    - ``litigation_excerpt`` — Legal Proceedings prose, diffed year-over-year
      by the assembly layer alongside MD&A and risk factors.
    """

    ticker: str
    form_type: str
    filed_at: datetime
    accession_no: str
    title: str = ""
    url: str = ""

    risk_factors_excerpt: str | None = Field(
        default=None,
        description=(
            "Full text of Item 1A (Risk Factors) when available. "
            "No truncation at fetch time — the assembly layer caps the rendered output."
        ),
    )
    mda_excerpt: str | None = Field(
        default=None,
        description=(
            "Full text of Item 7 (MD&A) when available. "
            "No truncation at fetch time — the assembly layer applies de-boilerplate "
            "diffing then caps the rendered output via max_filing_mda_chars."
        ),
    )

    litigation_excerpt: str | None = Field(
        default=None,
        description=(
            "Full text of the Legal Proceedings section when available "
            "(10-K Part I Item 3; 10-Q Part II Item 1).  No truncation at "
            "fetch time — the assembly layer applies prior-year diffing then "
            "caps the rendered output via max_filing_litigation_chars.  "
            "None for form types without the section (8-K) and for cache "
            "rows written before Phase 14."
        ),
    )

    # --- Phase 7 extensions (audit 2.7) — 8-K body capture ---
    # Populated by the edgar filings provider for 8-K forms only.
    body_excerpt: str | None = None             # first ~1,500 chars of main body
    items_8k: list[str] = Field(
        default_factory=list,
        description='Reported Item numbers, e.g. ["2.02", "9.01"].',
    )

    # --- Phase 13 extensions — period-of-report for fiscal pairing ---
    period_of_report: str | None = Field(
        default=None,
        description=(
            "Conformed period of report from SEC SGML header (YYYYMMDD), "
            "e.g. '20240930'.  Used to pair a current periodic filing with its "
            "prior-year counterpart for de-boilerplate diffing.  None when the "
            "SGML header omits this field (e.g. some older 8-Ks) or when the "
            "filing was read from a cache populated before Phase 13."
        ),
    )
