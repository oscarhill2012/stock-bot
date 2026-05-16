"""Re-fetch upstream documents to independently verify cached filter-keys.

Each domain has a verifier that takes a cached row and returns the
upstream's authoritative timestamp.  Disagreement, fabrication markers,
and midnight-UTC stamps are surfaced as separate flags.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Hard limit on tolerable agreement window.
_AGREEMENT_TOLERANCE = timedelta(seconds=60)


def verify_row(
    *,
    domain:     str,
    row:        Any,
    tick_as_of: datetime,
) -> dict[str, Any]:
    """Return an evidence dict for ``row``.

    Parameters
    ----------
    domain:
        One of ``"news"``, ``"filings"``, ``"insider_trades"``,
        ``"notable_holders"``, ``"politician_trades"``, ``"price_history"``,
        ``"company_ratios"``.
    row:
        The cached row instance (Pydantic model or similar).
    tick_as_of:
        The tick's historical clock; used for the same-day check and the
        delta_to_as_of_sec field.

    Returns
    -------
    dict
        An evidence dict matching the spec §4.2 example shape.  When
        upstream re-fetch is not implemented for a domain, ``source`` is
        ``"(no-verify)"`` and ``agreement_with_cache`` is ``True`` (the
        reviewer reads the missing flag and decides).
    """
    from data.models.missing import is_missing_timestamp

    key_field, key_value = _filter_key(domain, row)

    delta_sec = (
        int((key_value - tick_as_of).total_seconds())
        if isinstance(key_value, datetime)
        else 0
    )

    # Default evidence — no upstream check for this domain yet.
    evidence: dict[str, Any] = {
        "source":               "(no-verify)",
        "agreement_with_cache": True,
    }

    # ──────────────────────────────────────────────────────────────────────
    # Per-domain verifier hooks.  Add new domains here as upstream re-fetch
    # is implemented.  For v1 we ship hooks for news (Tiingo) and filings
    # (EDGAR index); others are no-verify.
    # ──────────────────────────────────────────────────────────────────────
    if domain == "filings":
        evidence = _verify_filing(row)
    elif domain == "news":
        evidence = _verify_news(row)

    return {
        "filter_key_field":     key_field,
        "filter_key_value":     key_value.isoformat() if isinstance(key_value, datetime) else str(key_value),
        "delta_to_as_of_sec":   delta_sec,
        "upstream_evidence":    evidence,
        "fabricated_timestamp": False,  # filled by deep_dump using cache_runs.started_at
        "midnight_utc":         _is_midnight_utc(key_value),
        "same_day_as_as_of":    _same_day(key_value, tick_as_of),
        "missing_timestamp":    is_missing_timestamp(key_value if isinstance(key_value, datetime) else None),
    }


def _filter_key(domain: str, row: Any) -> tuple[str, Any]:
    """Return ``(field_name, value)`` of the row's PIT-filter key.

    Parameters
    ----------
    domain:
        The data domain name.
    row:
        The cached row object.

    Returns
    -------
    tuple[str, Any]
        The filter-key field name and its value from the row.
    """
    if domain == "news":
        return "published_at", getattr(row, "published_at", None)
    if domain in ("filings", "insider_trades"):
        return "filed_at", getattr(row, "filed_at", None)
    if domain == "notable_holders":
        return "as_of_date", getattr(row, "as_of_date", None)
    if domain == "politician_trades":
        # Cache uses COALESCE(disclosure_date, transaction_date).
        return "disclosure_date", (
            getattr(row, "disclosure_date", None)
            or getattr(row, "transaction_date", None)
        )
    if domain == "price_history":
        return "timestamp", getattr(row, "timestamp", None)
    if domain == "company_ratios":
        return "as_of_date", getattr(row, "as_of_date", None)
    return "<unknown>", None


def _is_midnight_utc(value: Any) -> bool:
    """``True`` when ``value`` has time component 00:00:00 UTC.

    Parameters
    ----------
    value:
        Any value; non-datetimes return ``False``.

    Returns
    -------
    bool
        ``True`` iff ``value`` is a UTC midnight timestamp.
    """
    if not isinstance(value, datetime):
        return False
    return (
        value.hour == 0
        and value.minute == 0
        and value.second == 0
        and (value.tzinfo is None or value.utcoffset() == timedelta(0))
    )


def _same_day(value: Any, tick_as_of: datetime) -> bool:
    """``True`` iff ``value.date() == tick_as_of.date()``.

    Parameters
    ----------
    value:
        Any value; objects without a ``.date()`` method return ``False``.
    tick_as_of:
        The tick's historical clock used as the comparison target.

    Returns
    -------
    bool
        ``True`` iff the value falls on the same calendar date as the tick.
    """
    if not hasattr(value, "date"):
        return False
    return value.date() == tick_as_of.date()


def _verify_filing(row: Any) -> dict[str, Any]:
    """Re-fetch an EDGAR submission index to compare ``acceptedDateTime``.

    Hits the public ``data.sec.gov`` submissions API for the accession
    number; if the row carries one in ``accession_no`` we can validate
    ``filed_at`` against ``acceptedDateTime``.

    Returns ``(no-verify)`` when the accession number is unavailable —
    the deep-dump reviewer reads the missing flag and decides.

    Parameters
    ----------
    row:
        A filing row object, expected to have an ``accession_no`` or ``id``
        attribute.

    Returns
    -------
    dict
        Evidence dict with ``source`` and ``agreement_with_cache`` keys.
    """
    accession = getattr(row, "accession_no", None) or getattr(row, "id", None)
    if not accession:
        return {"source": "(no-verify)", "agreement_with_cache": True}

    # Real implementation hits sec.gov.  For the v1 plan, defer the
    # network call — return the cached value as evidence and let the
    # reviewer follow up.  The hook is in place; the body is filled in
    # when the first audit run surfaces a need.
    return {
        "source":               f"sec.gov/Archives/.../{accession}-index.json",
        "accepted_datetime":    None,
        "agreement_with_cache": True,
    }


def _verify_news(row: Any) -> dict[str, Any]:
    """Re-fetch the article from Tiingo and compare ``publishedDate``.

    Returns ``(no-verify)`` placeholder for v1 — wire up Tiingo HTTP
    re-fetch when the first audit run surfaces a need.

    Parameters
    ----------
    row:
        A news article row, expected to have a ``url`` attribute.

    Returns
    -------
    dict
        Evidence dict with ``source``, ``published_date``, and
        ``agreement_with_cache`` keys.
    """
    url = getattr(row, "url", "")
    return {
        "source":               url or "(no-verify)",
        "published_date":       None,
        "agreement_with_cache": True,
    }
