"""`get_notable_holders` — SC 13D / 13G beneficial-ownership filings via `edgartools`.

Free EDGAR equivalent of Quiver's "smart money" feed: anyone crossing
5% beneficial ownership of an issuer must file a Schedule 13D (active
intent) or 13G (passive). Amendments (13D/A, 13G/A) signal a stake
change. Filer = the holder; subject = the issuer (our `ticker`).

Same identity / rate-limit pattern as `sec_insiders.py` and
`sec_filings.py`: needs `EDGAR_IDENTITY` in `.env`, capped at 10 req/sec.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from edgar import Company, set_identity

from ..models import NotableHolder
from ..rate_limit import EDGAR
from ..retry import with_retry
from ..settings import get_settings, require

_FORMS = ("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")


def _ensure_identity() -> None:
    s = get_settings()
    identity = require("EDGAR_IDENTITY", s.edgar_identity, "get_notable_holders")
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


def _classify(form_type: str) -> tuple[Literal["active", "passive", "unknown"], bool]:
    upper = form_type.upper()
    is_amendment = "/A" in upper
    if "13D" in upper:
        return "active", is_amendment
    if "13G" in upper:
        return "passive", is_amendment
    return "unknown", is_amendment


def _build(filing: Any, symbol: str) -> NotableHolder | None:
    form_type = str(getattr(filing, "form", "")).strip()
    if not form_type:
        return None

    filed_date = _coerce_date(getattr(filing, "filing_date", None)) or date.today()
    filed_at = datetime.combine(filed_date, datetime.min.time(), tzinfo=timezone.utc)

    # The filer of an SC 13D/G is the holder, not the issuer — that's
    # exactly the name we want for `holder`.
    holder = (
        getattr(filing, "company", None)
        or getattr(filing, "company_name", None)
        or getattr(filing, "filer", None)
        or "unknown"
    )
    accession = (
        getattr(filing, "accession_no", None)
        or getattr(filing, "accession_number", None)
        or ""
    )
    url = (
        getattr(filing, "filing_url", None)
        or getattr(filing, "homepage_url", None)
        or getattr(filing, "url", None)
    )

    intent, is_amendment = _classify(form_type)
    return NotableHolder(
        ticker=symbol,
        holder=str(holder),
        form_type=form_type,
        intent=intent,
        is_amendment=is_amendment,
        filed_at=filed_at,
        accession_no=str(accession),
        url=str(url) if url else None,
    )


@with_retry
def _list_holder_filings(symbol: str, lookback_days: int, limit: int) -> list[Any]:
    _ensure_identity()
    from_iso = (date.today() - timedelta(days=lookback_days)).isoformat()
    company = Company(symbol)
    filings = company.get_filings(form=list(_FORMS), filing_date=f"{from_iso}:")
    return list(filings.head(max(1, min(limit, 50))))


async def get_notable_holders(
    ticker: str,
    lookback_days: int = 180,
    limit: int = 20,
) -> list[NotableHolder]:
    """Recent SC 13D/13G (and amendment) filings naming `ticker` as subject.

    `lookback_days` defaults to 180 since these filings are infrequent
    relative to Form 4. `limit` caps how many we return after sorting.
    """
    symbol = ticker.upper()

    await EDGAR.acquire()
    filings = await asyncio.to_thread(_list_holder_filings, symbol, lookback_days, limit)

    out: list[NotableHolder] = []
    for filing in filings:
        try:
            built = _build(filing, symbol)
        except Exception:
            continue
        if built is not None:
            out.append(built)
    return out
