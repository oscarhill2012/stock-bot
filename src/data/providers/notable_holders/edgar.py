"""`fetch` — SC 13D / 13G beneficial-ownership filings via `edgartools`.

Free EDGAR equivalent of Quiver's "smart money" feed: anyone crossing
5% beneficial ownership of an issuer must file a Schedule 13D (active
intent) or 13G (passive). Amendments (13D/A, 13G/A) signal a stake
change. Filer = the holder; subject = the issuer (our `ticker`).

Same identity / rate-limit pattern as `filings/edgar.py`:
needs `EDGAR_IDENTITY` in `.env`, capped at 10 req/sec.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from edgar import Company, set_identity

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import NotableHolder

_FORMS = ("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")


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
    filed_at = datetime.combine(filed_date, datetime.min.time(), tzinfo=UTC)

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
def _list_holder_filings(
    symbol: str,
    lookback_days: int,
    limit: int,
    as_of: datetime,
) -> list[Any]:
    """List SC 13D/13G/13F filings naming ``symbol`` in ``(as_of - lookback, as_of]``.

    Anchored on ``as_of`` so backfill sees only filings that existed historically.
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    lower_iso = (as_of.date() - timedelta(days=lookback_days)).isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form=list(_FORMS), filing_date=f"{lower_iso}:{upper_iso}")
    return list(filings.head(max(1, min(limit, 50))))


@register(
    domain="notable_holders",
    name="edgar",
    upstream="edgar",
    rate_per_minute=600,
    burst=20,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 180,
    limit: int = 20,
    **_unused,
) -> list[NotableHolder]:
    """Recent SC 13D/13G (and amendment) filings naming ``ticker`` as subject.

    ``lookback_days`` defaults to 180 since these filings are infrequent
    relative to Form 4.  ``limit`` caps how many we return after sorting.
    """
    symbol = ticker.upper()

    filings = await asyncio.to_thread(
        _list_holder_filings, symbol, lookback_days, limit, as_of,
    )

    out: list[NotableHolder] = []
    for filing in filings:
        try:
            built = _build(filing, symbol)
        except Exception:
            continue
        if built is not None:
            out.append(built)
    return out
