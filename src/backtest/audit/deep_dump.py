"""Write the deep-audit JSONL plus a human-readable summary markdown file."""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from backtest.audit.upstream_verifier import verify_row


def build_deep_rows(
    *,
    captured:   dict[str, dict[str, list[Any]]],
    tick_as_of: datetime,
    analyst_attribution: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Walk every captured row and produce one deep-audit dict per row.

    Parameters
    ----------
    captured:
        ``AuditingStore.drain_captured()`` output.
    tick_as_of:
        The tick's historical clock.
    analyst_attribution:
        Optional ``{domain: [analyst_names]}`` so each row can be tagged
        with the analyst(s) that consumed it.  When ``None``, ``analyst``
        is set to the domain name as a fallback.

    Returns
    -------
    list[dict]
        Deep-row dicts matching the schema in spec §4.2.
    """
    rows_out: list[dict[str, Any]] = []

    for domain, by_ticker in captured.items():
        analysts = (analyst_attribution or {}).get(domain) or [domain]

        for ticker, rows in by_ticker.items():
            for idx, row in enumerate(rows):
                evidence_block = verify_row(
                    domain=domain, row=row, tick_as_of=tick_as_of,
                )

                for analyst in analysts:
                    rows_out.append({
                        "tick_as_of":           tick_as_of.isoformat(),
                        "analyst":              analyst,
                        "ticker":               ticker,
                        "domain":               domain,
                        "row_id":               getattr(row, "id", f"{ticker}:{idx}"),
                        **evidence_block,
                    })

    return rows_out


def write_deep_dump(
    *,
    audit_dir: Path,
    tick_slug: str,
    rows:      list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Write the JSONL + summary markdown files for one audited tick.

    Parameters
    ----------
    audit_dir:
        Target directory (typically ``runs/<run-id>/audit/``).
    tick_slug:
        Filename-safe tick identifier.
    rows:
        Deep-audit rows produced by ``build_deep_rows``.

    Returns
    -------
    tuple[Path, Path]
        ``(full_jsonl_path, summary_md_path)``.
    """
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Write one JSON object per line for easy streaming consumption.
    full_path = audit_dir / f"{tick_slug}.full.jsonl"
    full_path.write_text(
        "\n".join(json.dumps(r, default=str) for r in rows) + "\n",
        encoding="utf-8",
    )

    summary_path = audit_dir / f"{tick_slug}.summary.md"
    summary_path.write_text(_build_summary(rows), encoding="utf-8")

    return full_path, summary_path


def _build_summary(rows: list[dict[str, Any]]) -> str:
    """Render the human-readable tripwire summary as markdown.

    Parameters
    ----------
    rows:
        Deep-audit rows as produced by ``build_deep_rows``.

    Returns
    -------
    str
        Markdown-formatted tripwire summary.
    """
    total = len(rows)

    counts: Counter[str] = Counter()
    for r in rows:
        if r.get("fabricated_timestamp"):
            counts["fabricated_timestamp"] += 1
        if r.get("midnight_utc"):
            counts["midnight_utc"] += 1
        if r.get("same_day_as_as_of"):
            counts["same_day_as_as_of"] += 1
        if r.get("missing_timestamp"):
            counts["missing_timestamp"] += 1
        if not r.get("upstream_evidence", {}).get("agreement_with_cache", True):
            counts["upstream_disagreement"] += 1

    # Build each summary line with a warning emoji when the count is non-zero
    # and a green tick when everything is clean.
    def line(flag: str, label: str) -> str:
        """Format one tripwire line with an emoji indicator."""
        return f"- {'⚠️' if counts[flag] else '✅'} {counts[flag]} rows: {label}"

    return (
        "# Tripwire summary — deep audit\n\n"
        f"Total rows audited: **{total}**\n\n"
        + line("fabricated_timestamp",  "filter-key matches fill-time wall-clock (likely fabricated)") + "\n"
        + line("midnight_utc",          "filter-key has time component 00:00:00 UTC (date-only)") + "\n"
        + line("same_day_as_as_of",     "filter-key date == tick.as_of date") + "\n"
        + line("missing_timestamp",     "row carried the MISSING_TIMESTAMP sentinel (should be skipped before delivery)") + "\n"
        + line("upstream_disagreement", "cached value disagreed with upstream re-fetch by >60s") + "\n"
        + "\n"
        + "Inspect the corresponding `.full.jsonl` for the per-row evidence "
        + "when any ⚠️ flag fires.  Any ❌ flag means the backtest is not trusted.\n"
    )
