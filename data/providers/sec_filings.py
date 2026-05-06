"""`get_company_filings` — 10-K / 10-Q / 8-K via `edgartools` (free EDGAR).

Risk Factors (Item 1A) and MD&A (Item 7 / Part 1 Item 2) are extracted
from the parsed 10-K / 10-Q section index — same data sec-api.io's
ExtractorApi gave us, but free.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any

from edgar import Company, set_identity

from ..models import Filing
from ..rate_limit import EDGAR
from ..retry import with_retry
from ..settings import get_settings, require

_EXCERPT_CHARS = 2000

# Section keys per edgartools naming. 8-Ks have no stable RF/MD&A so
# they're skipped — we still return the metadata.
_SECTION_KEYS = {
    "10-K": {"risk_factors_excerpt": "part_i_item_1a", "mda_excerpt": "part_ii_item_7"},
    "10-Q": {"risk_factors_excerpt": "part_ii_item_1a", "mda_excerpt": "part_i_item_2"},
}


def _ensure_identity() -> None:
    s = get_settings()
    identity = require("EDGAR_IDENTITY", s.edgar_identity, "get_company_filings")
    set_identity(identity)


def _coerce_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


def _section_text(obj: Any, key: str) -> str | None:
    sections = getattr(obj, "sections", None)
    if sections is None:
        return None
    try:
        section = sections.get(key)
    except (AttributeError, TypeError):
        return None
    if section is None:
        return None
    text = section.text() if hasattr(section, "text") else str(section)
    if not text:
        return None
    text = text.strip()
    return text[:_EXCERPT_CHARS] if text else None


def _build_filing(filing: Any, symbol: str, include_excerpts: bool) -> Filing:
    form_type = str(getattr(filing, "form", ""))

    filed_date = _coerce_date(getattr(filing, "filing_date", None)) or date.today()
    filed_at = datetime.combine(filed_date, datetime.min.time(), tzinfo=timezone.utc)

    accession = (
        getattr(filing, "accession_no", None)
        or getattr(filing, "accession_number", None)
        or ""
    )
    url = (
        getattr(filing, "filing_url", None)
        or getattr(filing, "homepage_url", None)
        or getattr(filing, "url", None)
        or ""
    )
    title = (
        getattr(filing, "primary_doc_description", None)
        or getattr(filing, "company", None)
        or getattr(filing, "company_name", None)
        or form_type
    )

    risk: str | None = None
    mda: str | None = None
    if include_excerpts and form_type in _SECTION_KEYS:
        try:
            obj = filing.obj()
            keys = _SECTION_KEYS[form_type]
            risk = _section_text(obj, keys["risk_factors_excerpt"])
            mda = _section_text(obj, keys["mda_excerpt"])
        except Exception:
            risk = None
            mda = None

    return Filing(
        ticker=symbol,
        form_type=form_type,
        filed_at=filed_at,
        accession_no=str(accession),
        title=str(title),
        url=str(url),
        risk_factors_excerpt=risk,
        mda_excerpt=mda,
    )


@with_retry
def _list_filings(symbol: str, form_types: tuple[str, ...], limit: int) -> list[Any]:
    _ensure_identity()
    company = Company(symbol)
    filings = company.get_filings(form=list(form_types))
    return list(filings.head(max(1, min(limit, 50))))


@with_retry
def _build_filing_with_identity(filing: Any, symbol: str, include_excerpts: bool) -> Filing:
    _ensure_identity()
    return _build_filing(filing, symbol, include_excerpts)


async def get_company_filings(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    include_excerpts: bool = True,
) -> list[Filing]:
    """Latest `limit` filings of the given `form_types` for `ticker`.

    Acquires one EDGAR token for the index, then one per filing if
    excerpts are requested (each `filing.obj()` is one HTTP roundtrip).
    """
    symbol = ticker.upper()

    await EDGAR.acquire()
    filings = await asyncio.to_thread(_list_filings, symbol, form_types, limit)

    out: list[Filing] = []
    for filing in filings:
        if include_excerpts:
            await EDGAR.acquire()
        try:
            built = await asyncio.to_thread(
                _build_filing_with_identity, filing, symbol, include_excerpts
            )
        except Exception:
            continue
        out.append(built)
    return out
