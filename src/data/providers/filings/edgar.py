"""EDGAR 10-K/10-Q/8-K filings provider (rate-limited via registry)."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

from edgar import Company, set_identity

from data.registry import _LIMITERS, register
from data.retry import with_retry
from data.secrets import require_key

from ...models import Filing

_EXCERPT_CHARS = 2000

# Section keys per edgartools naming. 8-Ks have no stable RF/MD&A so
# they're skipped — we still return the metadata.
_SECTION_KEYS = {
    "10-K": {"risk_factors_excerpt": "part_i_item_1a", "mda_excerpt": "part_ii_item_7"},
    "10-Q": {"risk_factors_excerpt": "part_ii_item_1a", "mda_excerpt": "part_i_item_2"},
}


def _ensure_identity() -> None:
    set_identity(require_key("EDGAR_IDENTITY"))


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

    # A filing with no parseable date is an upstream gap — surface it as a
    # deliberate skip via MISSING_TIMESTAMP rather than substituting wall-clock.
    raw_date = _coerce_date(getattr(filing, "filing_date", None))
    if raw_date is None:
        from data.models.missing import MISSING_TIMESTAMP
        filed_at = MISSING_TIMESTAMP
    else:
        filed_at = datetime.combine(raw_date, datetime.min.time(), tzinfo=UTC)

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
def _list_filings(
    symbol: str,
    form_types: tuple[str, ...],
    limit: int,
    as_of: datetime,
) -> list[Any]:
    """List the most recent ``limit`` filings of ``form_types`` for ``symbol``, filed on or before ``as_of``.

    Uses the SEC's ``filing_date=":YYYY-MM-DD"`` upper-bound syntax so the
    backfill never sees filings that did not yet exist at ``as_of``.
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form=list(form_types), filing_date=f":{upper_iso}")
    return list(filings.head(max(1, min(limit, 50))))


@with_retry
def _build_filing_with_identity(filing: Any, symbol: str, include_excerpts: bool) -> Filing:
    _ensure_identity()
    return _build_filing(filing, symbol, include_excerpts)


@register(
    domain="filings",
    name="edgar",
    upstream="edgar",
    rate_per_minute=600,
    burst=20,
)
async def fetch(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    as_of: datetime,
    include_excerpts: bool = True,
    **_unused,
) -> list[Filing]:
    """Latest ``limit`` filings of ``form_types`` for ``ticker`` filed on or before ``as_of``."""
    symbol = ticker.upper()

    # The registry's dispatch already acquired one EDGAR token for the
    # index fetch. Per-filing fetches require additional tokens.
    filings = await asyncio.to_thread(
        _list_filings, symbol, form_types, limit, as_of,
    )

    out: list[Filing] = []
    for filing in filings:
        if include_excerpts:
            await _LIMITERS["edgar"].acquire()  # per-filing HTTP roundtrip
        try:
            built = await asyncio.to_thread(
                _build_filing_with_identity, filing, symbol, include_excerpts
            )
        except Exception:
            continue
        out.append(built)
    return out
