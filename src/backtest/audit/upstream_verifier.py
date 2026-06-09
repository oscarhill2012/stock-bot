"""Re-fetch upstream documents to independently verify cached filter-keys.

Each domain has a verifier that takes a cached row and returns the
upstream's authoritative timestamp.  Disagreement, fabrication markers,
and midnight-UTC stamps are surfaced as separate flags.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Hard limit on tolerable agreement window.  Reserved for the real sec.gov /
# Tiingo verifier bodies (Plan 10 follow-up); the current placeholder bodies
# self-report "skip" and do not consult it yet.
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
        An evidence dict matching the spec §4.2 example shape.  The
        ``verification_status`` field is a tri-state string:

        * ``"ok"``       — verifier ran and upstream matched the cache.
        * ``"disagree"`` — verifier ran and upstream contradicted the cache.
        * ``"skip"``     — verifier did not run (no upstream verifier for
                           this domain, missing identifier, or placeholder
                           body not yet wired to the network).

        A ``skip`` must NEVER be rendered as verified in the SUMMARY.
    """
    from data.models.missing import is_missing_timestamp

    key_field, key_value = _filter_key(domain, row)

    delta_sec = (
        int((key_value - tick_as_of).total_seconds())
        if isinstance(key_value, datetime)
        else 0
    )

    # Default evidence — no upstream check for this domain yet.  Self-report
    # as a skip so the SUMMARY never renders an un-run verifier as verified.
    evidence: dict[str, Any] = {
        "source":              "(no-verify)",
        "verification_status": "skip",
        "reason":              "no upstream verifier for this domain",
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
    """Verify a filing row's ``filed_at`` against the SEC submissions API.

    Returns a tri-state ``verification_status`` instead of the old
    ``agreement_with_cache`` boolean (Plan 10 §4 — no green-on-skip).

    * ``"ok"``       — verifier ran and the upstream matched the cache.
    * ``"disagree"`` — verifier ran and the upstream contradicted the cache.
    * ``"skip"``     — verifier did not run (no accession, network
                       disabled, placeholder body).

    Parameters
    ----------
    row:
        A filing row object, expected to have an ``accession_no`` or
        ``id`` attribute.

    Returns
    -------
    dict
        ``{"source": str, "verification_status": str, ...}``.
    """
    accession = getattr(row, "accession_no", None) or getattr(row, "id", None)
    if not accession:
        # No identifier — cannot verify.  Skip, do not pretend to pass.
        return {
            "source":              "(no-verify)",
            "verification_status": "skip",
            "reason":              "missing accession_no/id",
        }

    # TODO Plan 10 follow-up: implement the sec.gov fetch.  Until then,
    # self-report as skip so the SUMMARY never renders an un-run verifier
    # as green.
    return {
        "source":              f"sec.gov/Archives/.../{accession}-index.json",
        "accepted_datetime":   None,  # accepted_datetime stays None until the real sec.gov fetch is wired.
        "verification_status": "skip",
        "reason":              "verifier not yet implemented",
    }


def _verify_news(row: Any) -> dict[str, Any]:
    """Verify a news article's ``published_at`` against Tiingo.

    Same tri-state contract as ``_verify_filing`` — see its docstring.

    Parameters
    ----------
    row:
        A news article row, expected to have a ``url`` attribute.

    Returns
    -------
    dict
        ``{"source": str, "verification_status": str, ...}``.
    """
    url = getattr(row, "url", "")
    if not url:
        return {
            "source":              "(no-verify)",
            "verification_status": "skip",
            "reason":              "missing url",
        }

    # TODO Plan 10 follow-up: implement the Tiingo fetch.  Until then,
    # self-report as skip — never green-on-placeholder.
    return {
        "source":              url,
        "published_date":      None,  # published_date stays None until the real Tiingo fetch is wired.
        "verification_status": "skip",
        "reason":              "verifier not yet implemented",
    }
