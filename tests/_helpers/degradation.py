"""Loud-fail assertion for happy-path pipeline tests.

See ``docs/test-policy.md`` §A.7 / §G.7 / §G.8.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable


# Domain key regex — matches '{domain}_verdicts' / '{domain}_evidence'.
# Introspects the state dict rather than hard-coding the eight live
# analyst domains, so a future domain is covered automatically.
_VERDICTS_KEY_RE = re.compile(r"^([a-z_]+)_verdicts$")
_EVIDENCE_KEY_RE = re.compile(r"^([a-z_]+)_evidence$")

# Warning substrings forbidden on a happy-path tick.
_FORBIDDEN_WARNING_SUBSTRINGS: tuple[str, ...] = (
    "branch_failed",
    "_fetch_failed",
    "snapshot_spy_fetch_failed",
    "usage_metadata_error",
)


def assert_no_silent_degradation(
    state: dict[str, object],
    *,
    allow_degradation: tuple[str, ...] = (),
) -> None:
    """Assert no domain in ``state`` silently neutral-fell.

    Walks every ``{domain}_verdicts`` and ``{domain}_evidence`` entry
    in ``state`` and asserts no row carries ``is_no_data=True`` unless
    its domain is named in ``allow_degradation``.

    Also walks ``caplog.records`` (via the active root logger handler
    set) and asserts no record's message contains any
    ``_FORBIDDEN_WARNING_SUBSTRINGS`` token unless the domain prefix
    is in ``allow_degradation``.

    :param state: tick-state dict produced by a pipeline run.
    :param allow_degradation: domain names that may legitimately
        carry is_no_data=True for this test (e.g. ``("news",)`` for
        a "news API down" regression test).
    :raises AssertionError: on any silent-failure signal.
    """
    allowed = set(allow_degradation)

    # Walk verdicts.
    for key, value in state.items():
        m = _VERDICTS_KEY_RE.match(key)
        if not m or m.group(1) in allowed:
            continue
        domain = m.group(1)
        rows = _coerce_rows(value)
        for row in rows:
            if _row_is_no_data(row):
                raise AssertionError(
                    f"silent degradation: {key} row has is_no_data=True "
                    f"(domain={domain}, row={row!r}); pass "
                    f"allow_degradation=({domain!r},) if intentional."
                )

    # Walk evidence — the per-ticker verdict lives at row["verdict"].
    for key, value in state.items():
        m = _EVIDENCE_KEY_RE.match(key)
        if not m or m.group(1) in allowed:
            continue
        domain = m.group(1)
        for row in _coerce_rows(value):
            verdict = row.get("verdict") if isinstance(row, dict) else None
            if verdict and _row_is_no_data(verdict):
                raise AssertionError(
                    f"silent degradation: {key} row.verdict has "
                    f"is_no_data=True (domain={domain}, row={row!r})."
                )

    # Walk warning records on the root logger's caplog handler.
    forbidden = _find_forbidden_warnings(allowed)
    if forbidden:
        joined = "\n  - ".join(forbidden)
        raise AssertionError(
            f"silent degradation: forbidden WARNING records seen:\n  - {joined}"
        )


def _coerce_rows(value: object) -> Iterable[dict]:
    """Tolerate list-of-dict or dict-of-dict shapes (joiners use both)."""
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        return [r for r in value.values() if isinstance(r, dict)]
    return []


def _row_is_no_data(row: dict) -> bool:
    """Read is_no_data from a row whether it's a dict or a Pydantic model dump."""
    val = row.get("is_no_data")
    if isinstance(val, bool):
        return val
    # Pydantic v2 dumps booleans as bool already; tolerate stringy "true".
    if isinstance(val, str):
        return val.lower() == "true"
    return False


def _find_forbidden_warnings(allowed_domains: set[str]) -> list[str]:
    """Scan the root logger's handler stack for forbidden WARNING records."""
    # caplog attaches a handler to the root logger.  Walk it.
    matches: list[str] = []
    for handler in logging.getLogger().handlers:
        records = getattr(handler, "records", None)
        if not records:
            continue
        for rec in records:
            if rec.levelno < logging.WARNING:
                continue
            msg = rec.getMessage()
            for token in _FORBIDDEN_WARNING_SUBSTRINGS:
                if token in msg:
                    # Tolerate allow_degradation: skip if any allowed
                    # domain name appears in the message.
                    if any(d in msg for d in allowed_domains):
                        continue
                    matches.append(f"[{rec.name}] {msg}")
                    break
    return matches
