"""`fetch` — SC 13D / 13G beneficial-ownership filings via `edgartools`.

Free EDGAR equivalent of Quiver's "smart money" feed: anyone crossing
5% beneficial ownership of an issuer must file a Schedule 13D (active
intent) or 13G (passive). Amendments (13D/A, 13G/A) signal a stake
change. Filer = the holder; subject = the issuer (our `ticker`).

Same identity / rate-limit pattern as `filings/edgar.py`:
needs `EDGAR_IDENTITY` in `.env`, capped at 10 req/sec.

Body-parsing (Task 4.3)
-----------------------
Each filing body is fetched via ``filing.text()`` to extract:

- ``percent_of_class`` — cover-page table field, both 13D and 13G.
- ``shares_held``      — cover-page table field, both 13D and 13G.
- ``purpose_excerpt``  — Item 4 prose, 13D only (≤ 2,000 chars); None for 13G.

Each body fetch is an additional EDGAR HTTP roundtrip guarded by
``_LIMITERS["edgar"]``.  The doubled cost is intentionally accepted per
the Phase 7 spec (one-shot cache fill; live runtime sees negligible impact).
"""
from __future__ import annotations

import asyncio
import re
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from edgar import Company, set_identity

from data.registry import _LIMITERS, register
from data.retry import with_retry
from data.secrets import require_key

from ...models import NotableHolder

_FORMS = ("SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A")

# Maximum characters to retain from Item 4 "Purpose of Transaction" prose.
_PURPOSE_EXCERPT_MAX = 2_000

# ---------------------------------------------------------------------------
# Body-parsing regex constants
# ---------------------------------------------------------------------------

# Matches the "Percent of Class" row on the SC 13D/G cover page.
# Handles optional colon/dash separator and a trailing "%" sign.
# Example: "Percent of Class: 8.5%"  or  "Percent of class - 12.34 %"
_RE_PERCENT_OF_CLASS = re.compile(
    r"Percent of [Cc]lass\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
)

# Matches the "Shares Held" (or "Amount Beneficially Owned") row.
# Digits may be comma-separated (e.g. "1,200,000").
# Example: "Shares Held: 1,200,000"
_RE_SHARES_HELD = re.compile(
    r"Shares Held\s*[:\-]?\s*([0-9][0-9,\.]*)",
)

# Captures everything between "Item 4. Purpose of Transaction" and the next
# item header (Item 5) or a Signature block.  DOTALL so "." crosses newlines.
_RE_ITEM_4 = re.compile(
    r"Item\s+4\.?\s*Purpose of Transaction.*?(?=Item\s+5\.|Signature)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_cover_page(body: str) -> tuple[float | None, float | None]:
    """Extract ``percent_of_class`` and ``shares_held`` from a filing body.

    Scans the raw text for cover-page table fields that appear in both
    SC 13D and SC 13G filings.  Returns ``None`` for each field where no
    match is found.

    Parameters
    ----------
    body:
        Full plain-text body of the EDGAR filing.

    Returns
    -------
    tuple of (percent_of_class, shares_held):
        Both values are floats or ``None`` if not found.
    """
    pct_m    = _RE_PERCENT_OF_CLASS.search(body)
    shares_m = _RE_SHARES_HELD.search(body)

    pct    = float(pct_m.group(1)) if pct_m else None
    shares = float(shares_m.group(1).replace(",", "")) if shares_m else None

    return pct, shares


def _parse_purpose_excerpt(body: str) -> str | None:
    """Extract a bounded excerpt of Item 4 "Purpose of Transaction" prose.

    Only relevant for SC 13D filings (active intent); callers are responsible
    for passing ``None`` through for SC 13G.

    Parameters
    ----------
    body:
        Full plain-text body of the EDGAR filing.

    Returns
    -------
    str or None:
        Up to ``_PURPOSE_EXCERPT_MAX`` characters of the Item 4 block,
        or ``None`` if the block is not found.
    """
    m = _RE_ITEM_4.search(body)
    if not m:
        return None

    text = m.group(0).strip()
    return text[:_PURPOSE_EXCERPT_MAX]


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

    # A 13D/G filing with no parseable date is an upstream gap — surface it
    # as a deliberate skip via MISSING_TIMESTAMP rather than substituting
    # wall-clock.
    raw_date = _coerce_date(getattr(filing, "filing_date", None))
    if raw_date is None:
        from data.models.missing import MISSING_TIMESTAMP
        filed_at = MISSING_TIMESTAMP
    else:
        filed_at = datetime.combine(raw_date, datetime.min.time(), tzinfo=UTC)

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

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol.
    lookback_days:
        Number of calendar days to look back from ``as_of``.
    limit:
        Maximum number of raw filing objects to return (capped at 50).
    as_of:
        Upper-bound datetime — filings after this date are excluded.
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    lower_iso = (as_of.date() - timedelta(days=lookback_days)).isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form=list(_FORMS), filing_date=f"{lower_iso}:{upper_iso}")
    return list(filings.head(max(1, min(limit, 50))))


def _iter_filings(
    symbol: str,
    lookback_days: int,
    limit: int,
    as_of: datetime,
) -> list[Any]:
    """Thin seam around ``_list_holder_filings`` that tests can monkeypatch.

    Returns the raw edgartools filing objects for ``symbol`` without any model
    conversion — ``fetch`` handles that loop.  Keeping this as a plain
    synchronous function makes monkeypatching trivial.

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol.
    lookback_days:
        Number of calendar days to look back from ``as_of``.
    limit:
        Maximum number of raw filing objects to return.
    as_of:
        Upper-bound datetime — filings after this date are excluded.
    """
    return _list_holder_filings(symbol, lookback_days, limit, as_of)


def _fetch_body(filing: Any) -> str:
    """Fetch the plain-text body of a filing via edgartools' ``.text()`` method.

    Returns an empty string if the filing object does not support ``.text()``
    or if the call raises — body parsing degrades gracefully to ``None`` fields.

    Parameters
    ----------
    filing:
        Raw edgartools filing object.
    """
    try:
        body = filing.text()
        return body if isinstance(body, str) else (body or "")
    except Exception:
        return ""


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

    For each filing, the body text is fetched (one additional EDGAR HTTP
    roundtrip per filing, guarded by ``_LIMITERS["edgar"]``) to extract:

    - ``percent_of_class`` and ``shares_held`` from the cover-page table.
    - ``purpose_excerpt`` from Item 4 prose (SC 13D only; ≤ 2,000 chars).

    ``lookback_days`` defaults to 180 since these filings are infrequent
    relative to Form 4.  ``limit`` caps how many we return after sorting.

    Parameters
    ----------
    ticker:
        Stock ticker symbol (case-insensitive).
    as_of:
        Upper-bound datetime — only filings up to this point are returned.
    lookback_days:
        How many calendar days to look back from ``as_of``.
    limit:
        Maximum number of filings to return.
    **_unused:
        Absorbs extra kwargs dispatched by the registry (e.g. ``per_form``,
        ``from_date``) that other providers use but this one does not.
    """
    symbol = ticker.upper()

    # Use the _iter_filings seam so tests can monkeypatch the EDGAR call.
    filings = await asyncio.to_thread(
        _iter_filings, symbol, lookback_days, limit, as_of,
    )

    out: list[NotableHolder] = []
    for filing in filings:
        try:
            built = _build(filing, symbol)
        except Exception:
            continue

        if built is None:
            continue

        # --- Body fetch (one extra EDGAR roundtrip per filing) ---------------
        # Acquire a rate-limit token before the HTTP call so we stay within
        # the 10 req/s SEC cap shared across all EDGAR providers.
        await _LIMITERS["edgar"].acquire()
        body = await asyncio.to_thread(_fetch_body, filing)

        # Parse cover-page fields (valid for both 13D and 13G).
        pct, shares = _parse_cover_page(body)

        # Item 4 "Purpose of Transaction" is a 13D-only disclosure.
        is_13d = "13D" in built.form_type.upper()
        purpose = _parse_purpose_excerpt(body) if is_13d else None

        # Rebuild the holder with the newly parsed fields.  NotableHolder is
        # a Pydantic model, so we use model_copy to avoid mutating the
        # original object and to keep field validation in place.
        out.append(built.model_copy(update={
            "percent_of_class": pct,
            "shares_held":      shares,
            "purpose_excerpt":  purpose,
        }))

    return out
