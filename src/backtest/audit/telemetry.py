"""Per-tick telemetry record — built and written by the driver.

The record schema is documented in the PIT-correctness and audit design spec
§4.1.  Each record is ~5 KB; a 20-trading-day, two-ticks/day window produces
~200 KB total.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.audit.tripwires import compute_tripwires
from backtest.schedule import Tick


def build_telemetry_record(
    *,
    tick:                      Tick,
    run_id:                    str,
    strict_mode:               bool,
    per_domain:                dict[str, dict[str, Any]],
    report_cache_hits:         list[dict[str, Any]],
    db_writes_recorded_at:     dict[str, dict[str, Any]],
    wall_clock_fallback_fired: bool = False,
) -> dict[str, Any]:
    """Assemble the audit-log telemetry record for one tick.

    Parameters
    ----------
    tick:
        The scheduled tick (``as_of`` + ``phase``).
    run_id:
        Stable run identifier; combined with tick info to produce ``tick_id``.
    strict_mode:
        ``True`` iff ``STOCKBOT_STRICT_AS_OF=1`` was active for this tick.
    per_domain:
        ``{domain_name: {provider, ticker_rows: {ticker: {...}}}}``.
        Domain-specific row summaries — built by the driver from
        per-domain hooks.
    report_cache_hits:
        ``[{analyst, ticker, input_hash, originating_as_of}, ...]`` — one
        entry per cache hit during the tick.
    db_writes_recorded_at:
        ``{row_type: {count, matches_as_of}}`` — DB-row stamp check.
    wall_clock_fallback_fired:
        Forwarded into ``compute_tripwires``.  Always ``False`` in strict
        mode (the run would have aborted).

    Returns
    -------
    dict
        The full telemetry record, ready to JSON-serialise.
    """
    tick_id = f"{run_id}-{tick.as_of.isoformat()}-{tick.phase}"

    tripwires = compute_tripwires(
        as_of=tick.as_of,
        phase=tick.phase,
        per_domain=per_domain,
        wall_clock_fallback_fired=wall_clock_fallback_fired,
    )

    return {
        "tick_id":               tick_id,
        "as_of":                 tick.as_of.isoformat(),
        "phase":                 tick.phase,
        "strict_mode":           strict_mode,
        "tripwires":             tripwires,
        "per_domain":            per_domain,
        "report_cache_hits":     report_cache_hits,
        "db_writes_recorded_at": db_writes_recorded_at,
    }


def write_telemetry_record(audit_dir: Path, record: dict[str, Any]) -> Path:
    """Write ``record`` to ``<audit_dir>/<tick-slug>.tick.json``.

    Creates ``audit_dir`` if it does not exist.  The tick-slug is derived
    from the record's ``tick_id`` by replacing characters that are unsafe
    on common filesystems.

    Parameters
    ----------
    audit_dir:
        Directory under ``runs/<run-id>/audit/``.
    record:
        Record produced by ``build_telemetry_record``.

    Returns
    -------
    Path
        The path written to.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)

    slug = (
        str(record["tick_id"])
        .replace(":", "-")
        .replace("+", "p")
        .replace(" ", "T")
        .replace("/", "_")
    )
    path = audit_dir / f"{slug}.tick.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    return path


def per_domain_from_store_reads(
    *,
    cache_reads: dict[str, dict[str, list[Any]]],
    as_of:       datetime,
    phase:       str,
) -> dict[str, dict[str, Any]]:
    """Summarise cache-store reads into the ``per_domain`` shape.

    Walks the captured read log and produces, per (domain, ticker), the
    count, min/max filter-key timestamps, sentinel counts, and (for
    ``price_history``) whether a same-day bar was seen.

    Parameters
    ----------
    cache_reads:
        ``{domain: {ticker: [rows]}}`` capture from the driver's
        per-tick read hook.
    as_of:
        The tick's historical clock — used for the OHLCV same-day check.
    phase:
        ``"open"`` or ``"close"``.  Recorded for context only.

    Returns
    -------
    dict
        The ``per_domain`` block of the telemetry record.
    """
    from data.models.missing import is_missing_timestamp

    out: dict[str, dict[str, Any]] = {}

    for domain, by_ticker in cache_reads.items():
        domain_block: dict[str, Any] = {
            "provider":    "cache",
            "ticker_rows": {},
        }

        for ticker, rows in by_ticker.items():
            # Pick the right filter-key field per domain.
            field_map = {
                "price_history":     "timestamp",
                "news":              "published_at",
                "filings":           "filed_at",
                "insider_trades":    "filed_at",
                "notable_holders":   "as_of_date",
                "politician_trades": "disclosure_date",
                "company_ratios":    "as_of_date",
            }
            key_field = field_map.get(domain)

            count                   = len(rows)
            min_key:        Any     = None
            max_key:        Any     = None
            midnight_count          = 0
            missing_count           = 0
            sameday_bar_seen        = False

            for row in rows:
                value = getattr(row, key_field, None) if key_field else None
                if value is None:
                    continue

                if is_missing_timestamp(value if isinstance(value, datetime) else None):
                    missing_count += 1
                    continue

                iso = value.isoformat() if hasattr(value, "isoformat") else str(value)

                if min_key is None or iso < min_key:
                    min_key = iso
                if max_key is None or iso > max_key:
                    max_key = iso

                if hasattr(value, "hour") and value.hour == 0 and value.minute == 0:
                    midnight_count += 1

                if (
                    domain == "price_history"
                    and hasattr(value, "date")
                    and value.date() == as_of.date()
                ):
                    sameday_bar_seen = True

            ticker_block: dict[str, Any] = {"count": count}

            # Use the same key-field naming convention as the spec sample.
            if domain == "price_history":
                ticker_block["min_ts"]            = min_key
                ticker_block["max_ts"]            = max_key
                ticker_block["sameday_bar_seen"]  = sameday_bar_seen
            elif domain == "news":
                ticker_block["min_published_at"]        = min_key
                ticker_block["max_published_at"]        = max_key
                ticker_block["midnight_utc_count"]      = midnight_count
                ticker_block["missing_timestamp_count"] = missing_count
            elif domain in ("filings", "insider_trades"):
                ticker_block["min_filed_at"]            = min_key
                ticker_block["max_filed_at"]            = max_key
                ticker_block["midnight_utc_count"]      = midnight_count
                ticker_block["missing_timestamp_count"] = missing_count
            elif domain == "politician_trades":
                ticker_block["min_disclosure_at"]       = min_key
                ticker_block["max_disclosure_at"]       = max_key
                ticker_block["missing_timestamp_count"] = missing_count
            else:
                # Generic fallback for any domain we haven't special-cased.
                ticker_block["min_key"] = min_key
                ticker_block["max_key"] = max_key

            domain_block["ticker_rows"][ticker] = ticker_block

        out[domain] = domain_block

    return out
