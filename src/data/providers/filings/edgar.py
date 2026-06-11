"""EDGAR 10-K/10-Q/8-K filings provider (rate-limited via registry).

2026-06-11 redesign — form-aware fetching instead of "latest N filings":

- **Live mode** (no ``from_date``): three per-form queries — the latest
  10-K, the latest 10-Q, and every 8-K within the staleness horizon — then
  the shared ``select_current_filings`` rule from ``data.filing_selection``
  decides what the analyst sees.  The same rule runs on the backtest cache
  read path, so live and replay cannot drift apart.

- **Backfill mode** (``from_date`` given): returns the raw superset a
  backtest cache needs to serve *any* tick in ``[from_date, as_of]`` —
  every filing of the three forms inside that range, plus the anchor set
  as of ``from_date`` (latest 10-K, latest 10-Q, and pre-window 8-Ks still
  inside the staleness horizon at the window start).  **No selection** is
  applied: superseded periodic filings are kept deliberately, because
  selection happens per-tick at replay-read time via the shared rule.

The old implementation fetched the "latest ``limit`` filings as of
fetch-time" with an upper-bound-only date filter and a ``head()`` cap,
which silently truncated long-window backfills (found in the 2026-06-11
cache audit) and diverged from the replay read path.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Any

from edgar import Company, set_identity

from data.filing_selection import (
    _EVENT_FORM,
    _PERIODIC_FORMS,
    select_current_filings,
)
from data.registry import _LIMITERS, register
from data.retry import with_retry
from data.secrets import require_key

from ...models import Filing

_EXCERPT_CHARS = 2000
# Maximum chars captured for the 8-K body excerpt (Phase 7 — audit 2.7).
_BODY_EXCERPT_CHARS = 1500

# Every form the analyst-visibility rule knows about — the only forms worth
# fetching or caching.
_ALL_FORMS: tuple[str, ...] = (*_PERIODIC_FORMS, _EVENT_FORM)

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
def _list_latest_filing(symbol: str, form: str, as_of: datetime) -> list[Any]:
    """List the single most recent ``form`` filing on or before ``as_of``.

    Uses the SEC's ``filing_date=":YYYY-MM-DD"`` upper-bound syntax so the
    query never sees filings that did not yet exist at ``as_of``.

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol.
    form:
        A single SEC form type, e.g. ``"10-K"``.
    as_of:
        Upper-bound datetime — filings after this date are excluded.

    Returns
    -------
    list[Any]
        At most one raw edgartools filing object (empty if the company has
        never filed that form).
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form=form, filing_date=f":{upper_iso}")
    return list(filings.head(1))


@with_retry
def _list_filings_range(
    symbol: str,
    forms: tuple[str, ...],
    lower: datetime,
    upper: datetime,
) -> list[Any]:
    """List every filing of ``forms`` filed within ``[lower, upper]``.

    Both bounds are inclusive (SEC ``filing_date="YYYY-MM-DD:YYYY-MM-DD"``
    range syntax).  No count cap is applied — the date range itself bounds
    the result, which is exactly the property the old ``head()`` cap broke.

    Parameters
    ----------
    symbol:
        Uppercase ticker symbol.
    forms:
        SEC form types to include, e.g. ``("8-K",)``.
    lower:
        Inclusive lower-bound datetime.
    upper:
        Inclusive upper-bound datetime.
    """
    _ensure_identity()
    span    = f"{lower.date().isoformat()}:{upper.date().isoformat()}"
    company = Company(symbol)
    filings = company.get_filings(form=list(forms), filing_date=span)
    return list(filings)


def _iter_latest_filing(symbol: str, form: str, as_of: datetime) -> list[Any]:
    """Thin seam around ``_list_latest_filing`` that tests can monkeypatch.

    Returns raw edgartools filing objects without model conversion —
    ``fetch`` handles that loop.  Kept as a plain synchronous function (not
    async) so monkeypatching is trivial.
    """
    return _list_latest_filing(symbol, form, as_of)


def _iter_filings_range(
    symbol: str,
    forms: tuple[str, ...],
    lower: datetime,
    upper: datetime,
) -> list[Any]:
    """Thin seam around ``_list_filings_range`` that tests can monkeypatch.

    Returns raw edgartools filing objects without model conversion —
    ``fetch`` handles that loop.  Kept as a plain synchronous function (not
    async) so monkeypatching is trivial.
    """
    return _list_filings_range(symbol, forms, lower, upper)


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


def _accession_key(filing: Any) -> str:
    """Return a dedupe key for a raw edgartools filing object.

    Prefers the accession number (unique per filing across all EDGAR
    queries); falls back to object identity for malformed rows so a missing
    accession never collapses two distinct filings into one.
    """
    accession = (
        getattr(filing, "accession_no", None)
        or getattr(filing, "accession_number", None)
    )
    return str(accession) if accession else f"id:{id(filing)}"


@register(
    domain="filings",
    name="edgar",
    upstream="edgar",
    rate_per_minute=600,
    burst=20,
)
async def fetch(
    ticker: str,
    *,
    as_of: date | datetime,
    staleness_days: int = 90,
    include_excerpts: bool = True,
    from_date: date | None = None,
    **_unused,
) -> list[Filing]:
    """Fetch analyst-visible filings (live) or a cacheable superset (backfill).

    Parameters
    ----------
    ticker:
        Stock ticker symbol (case-insensitive).
    as_of:
        Upper-bound date or datetime — filings after this point are excluded.
        Accepts a plain ``date`` for convenience (treated as midnight UTC).
    staleness_days:
        8-K visibility horizon — an 8-K older than this (relative to
        ``as_of`` live, or to ``from_date`` for backfill anchors) is no
        longer analyst-visible.  Sourced from
        ``defaults.filings_8k_staleness_days`` by the dispatch wrapper.
    include_excerpts:
        When ``True`` (default), fetch section excerpts for 10-K and 10-Q
        forms and body text for 8-K forms.
    from_date:
        When given, switches to **backfill mode**: return every filing in
        ``[from_date, as_of]`` plus the anchor set as of ``from_date``,
        deduplicated by accession number, with **no** selection applied —
        the backtest cache stores the superset and the shared selection
        rule runs per-tick at replay-read time.  When absent (**live
        mode**), the shared ``select_current_filings`` rule is applied so
        the output matches exactly what replay would serve.

    Returns
    -------
    list[Filing]
        Live mode: the analyst-visible selection (latest 10-K, latest 10-Q,
        fresh 8-Ks), newest first.  Backfill mode: the raw cacheable
        superset, newest first.
    """
    symbol   = ticker.upper()
    as_of_dt = _coerce_as_of_to_datetime(as_of)

    # Collect raw edgartools filings from the per-form queries, deduplicating
    # by accession number (a filing can be caught by both a range query and
    # an anchor query in backfill mode).
    raw:  list[Any] = []
    seen: set[str]  = set()

    def _add(batch: list[Any]) -> None:
        """Append ``batch`` to ``raw``, skipping accessions already seen."""
        for filing in batch:
            key = _accession_key(filing)
            if key not in seen:
                seen.add(key)
                raw.append(filing)

    if from_date is None:
        # ── Live mode: fetch exactly the selection rule's candidates ───────
        # Latest instance of each periodic form, then the 8-K staleness pane.
        for form in _PERIODIC_FORMS:
            _add(await asyncio.to_thread(_iter_latest_filing, symbol, form, as_of_dt))

        horizon = as_of_dt - timedelta(days=staleness_days)
        _add(await asyncio.to_thread(
            _iter_filings_range, symbol, (_EVENT_FORM,), horizon, as_of_dt,
        ))
    else:
        # ── Backfill mode: in-window range plus anchors as of window start ─
        window_lower = datetime.combine(from_date, datetime.min.time(), tzinfo=UTC)

        # Everything filed inside the window itself, all three forms.
        _add(await asyncio.to_thread(
            _iter_filings_range, symbol, _ALL_FORMS, window_lower, as_of_dt,
        ))

        # Periodic anchors — whatever 10-K/10-Q was current at window start.
        for form in _PERIODIC_FORMS:
            _add(await asyncio.to_thread(
                _iter_latest_filing, symbol, form, window_lower,
            ))

        # Pre-window 8-Ks still inside the staleness horizon at window start,
        # so the first ticks of the window see the same events live would.
        _add(await asyncio.to_thread(
            _iter_filings_range,
            symbol,
            (_EVENT_FORM,),
            window_lower - timedelta(days=staleness_days),
            window_lower,
        ))

    # Convert raw edgartools objects into Filing models.  The registry's
    # dispatch already acquired one EDGAR token for the listing queries;
    # per-filing excerpt fetches acquire additional tokens below.
    out: list[Filing] = []
    for filing in raw:
        if include_excerpts:
            await _LIMITERS["edgar"].acquire()  # per-filing HTTP roundtrip
        try:
            built = await asyncio.to_thread(
                _build_filing_with_identity, filing, symbol, include_excerpts,
            )
        except Exception:
            continue
        out.append(built)

    if from_date is None:
        # Live mode — apply the shared analyst-visibility rule so this path
        # and the backtest cache read serve identical selections.
        return select_current_filings(
            out, as_of=as_of_dt, staleness_days=staleness_days,
        )

    # Backfill mode — raw superset, newest first for deterministic output.
    out.sort(key=lambda f: (f.filed_at, f.accession_no), reverse=True)
    return out
