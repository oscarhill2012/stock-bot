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
# Maximum chars captured for the 8-K body excerpt (Phase 7 — audit 2.7).
_BODY_EXCERPT_CHARS = 1500

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

    # --- 8-K specific fields (Phase 7 — audit 2.7) ---
    body_excerpt: str | None = None
    items_8k: list[str] = []

    if form_type == "8-K":
        # Capture a bounded slice of the raw body text so the Fundamental
        # LLM extractor can classify the event without fetching the full doc.
        body_text = ""
        try:
            body_text = filing.text() or ""
        except Exception:
            body_text = ""
        body_excerpt = body_text[:_BODY_EXCERPT_CHARS] if body_text else None

        # Smoke A6 gotcha: edgartools returns `filing.items` as a
        # comma-delimited STRING (e.g. "2.02,9.01"), not a Python list.
        # list(filing.items) would iterate char-by-char — must split on comma.
        raw_items = getattr(filing, "items", "") or ""
        items_8k = [
            part.strip()
            for part in str(raw_items).split(",")
            if part.strip()
        ]

    return Filing(
        ticker=symbol,
        form_type=form_type,
        filed_at=filed_at,
        accession_no=str(accession),
        title=str(title),
        url=str(url),
        risk_factors_excerpt=risk,
        mda_excerpt=mda,
        body_excerpt=body_excerpt,
        items_8k=items_8k,
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


def _iter_filings(
    symbol: str,
    form_types: tuple[str, ...],
    limit: int,
    as_of: datetime,
) -> list[Any]:
    """Thin seam around ``_list_filings`` that tests can monkeypatch.

    Returns the raw edgartools filing objects for ``symbol`` without any
    model conversion — ``fetch`` handles that loop.  Keeping this as a
    plain synchronous function (not async) makes monkeypatching trivial.

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol.
    form_types:
        Tuple of SEC form types to include, e.g. ``("10-K", "10-Q", "8-K")``.
    limit:
        Maximum number of filings to retrieve across all form types.
    as_of:
        Upper-bound date — filings after this date are excluded.
    """
    return _list_filings(symbol, form_types, limit, as_of)


@with_retry
def _build_filing_with_identity(filing: Any, symbol: str, include_excerpts: bool) -> Filing:
    """Wrap ``_build_filing`` with an EDGAR identity call when excerpts are needed.

    ``_ensure_identity()`` is called only when ``include_excerpts=True`` because
    that is the only path that makes outbound HTTP requests (via ``filing.obj()``
    for 10-K/10-Q section parsing).  Skipping the call when excerpts are
    disabled allows tests to monkeypatch the filings list and build ``Filing``
    model objects without any real credentials.

    Parameters
    ----------
    filing:
        Raw edgartools filing object.
    symbol:
        Uppercase ticker symbol.
    include_excerpts:
        When ``True``, authenticate and fetch section excerpts (risk factors,
        MD&A for 10-K/10-Q; body text for 8-K).
    """
    if include_excerpts:
        # Re-assert the EDGAR user-agent before the per-filing HTTP round-trip.
        # edgartools sometimes resets identity between requests in a batch.
        _ensure_identity()
    return _build_filing(filing, symbol, include_excerpts)


def _coerce_as_of_to_datetime(as_of: date | datetime) -> datetime:
    """Normalise ``as_of`` to a timezone-aware ``datetime``.

    ``fetch`` accepts either a plain ``date`` (common in test callers and
    backtest driver code) or a full ``datetime``.  This helper converts
    both forms to a UTC-aware ``datetime`` so downstream functions that
    expect ``datetime`` work correctly.

    Parameters
    ----------
    as_of:
        Either a ``datetime`` (possibly already timezone-aware) or a plain
        ``date`` that represents end-of-day in UTC.
    """
    if isinstance(as_of, datetime):
        return as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    # Plain date — treat as midnight UTC on that calendar day.
    return datetime.combine(as_of, datetime.min.time(), tzinfo=UTC)


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
    as_of: date | datetime,
    include_excerpts: bool = True,
    **_unused,
) -> list[Filing]:
    """Latest ``limit`` filings of ``form_types`` for ``ticker`` filed on or before ``as_of``.

    Parameters
    ----------
    ticker:
        Stock ticker symbol (case-insensitive).
    form_types:
        SEC form types to retrieve.  Defaults to 10-K, 10-Q, and 8-K.
    limit:
        Maximum total filings to retrieve.  Also accepted as ``per_form``
        via ``**_unused`` for callers that use per-form sizing semantics
        (the underlying SEC query applies the limit globally, not per form).
    as_of:
        Upper-bound date or datetime — filings after this point are excluded.
        Accepts a plain ``date`` for convenience (treated as midnight UTC).
    include_excerpts:
        When ``True`` (default), fetch section excerpts for 10-K and 10-Q
        forms and body text for 8-K forms.
    """
    symbol = ticker.upper()
    as_of_dt = _coerce_as_of_to_datetime(as_of)

    # Delegate to the _iter_filings seam so tests can monkeypatch the raw
    # edgartools call without needing to mock the entire Company class.
    # The registry's dispatch already acquired one EDGAR token for the
    # index fetch; per-filing fetches acquire additional tokens below.
    filings = await asyncio.to_thread(
        _iter_filings, symbol, form_types, limit, as_of_dt,
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
