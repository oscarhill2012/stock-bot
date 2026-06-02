"""Regression guard against bare state-key residue in test fixtures (A-014 / A-086).

Audit findings:
- A-014: bare ``state["positions"]`` / ``state["cash"]`` accesses in test
  fixtures seed values the live pipeline never reads, turning those tests into
  silent-regression fixtures (they assert nothing real).
- A-086: same problem with bare ``state["thesis"]``.

This guard scans ``tests/`` for the subscript and ``.get(`` forms of these
bare keys and asserts no un-allowlisted offenders remain.

Allowlist rationale (narrow, documented):
- ``test_no_bare_thesis_keys.py``  — the src/ guard; its own regex patterns and
  docstrings contain the bare-key literals by design.
- ``test_no_bare_state_keys_in_fixtures.py``  — THIS file; ``_PATTERN`` and this
  docstring contain the bare-key literals by design.
- ``test_decision_logger_held_view.py``  — the A-014 negative-control test;
  its docstrings explicitly document the bare ``state["positions"]`` key it
  proves is NOT read.  (``temp:executor_positions_bridge`` was removed from the
  executor in the Task 6 refactor; its seed here is a dead-key negative-control.)

No other path is on the allowlist.  If you need to add a new one, add it here
with a documented reason.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Pattern: subscript and .get( forms of the bare state keys we are auditing.
# Dict-key form (e.g. {"positions": ...}) is intentionally NOT matched — that
# is the legitimate Portfolio model shape, not a state accessor.
# ---------------------------------------------------------------------------

_PATTERN = re.compile(
    r"""state\[\s*["'](positions|cash|thesis)["']\s*\]"""
    r"""|state\.get\(\s*["'](positions|cash|thesis)["']"""
)

# ---------------------------------------------------------------------------
# Scan root — anchored on __file__ so the scan is cwd-independent.
# This file lives at tests/unit/, so parent.parent is tests/.
# ---------------------------------------------------------------------------

_TESTS_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Allowlist — resolved absolute paths to files that are permitted to contain
# the bare-key patterns (each carries a documented reason above).
# ---------------------------------------------------------------------------

_ALLOWLIST: frozenset[Path] = frozenset(
    {
        # The src/ guard file — contains the bare-key literals in its own patterns.
        _TESTS_ROOT / "unit" / "test_no_bare_thesis_keys.py",

        # This file itself — _PATTERN and the module docstring reference bare keys.
        _TESTS_ROOT / "unit" / "test_no_bare_state_keys_in_fixtures.py",

        # A-014 negative-control: documents the bare key the logger must NOT read.
        _TESTS_ROOT / "unit" / "backtest" / "test_decision_logger_held_view.py",
    }
)


def test_no_bare_state_keys_in_test_fixtures() -> None:
    """A-014 / A-086: no un-allowlisted bare-key subscript / .get( in tests/.

    Scans every ``*.py`` under ``tests/`` for the subscript and ``.get(`` forms
    of the bare ``positions``, ``cash``, and ``thesis`` state keys.  Excludes
    the narrow allowlist of files that legitimately reference the patterns (the
    src/ guard, this file itself, and the A-014 negative-control).

    A bare-key seed in a test fixture is a silent-regression fixture: the live
    pipeline reads the ``user:``-prefixed versions, so seeding the bare key
    asserts nothing real.

    Parameters
    ----------
    (no parameters — pytest calls this directly)

    Returns
    -------
    None
        Asserts an empty offender list; fails with a descriptive listing if any
        bare-key subscript / ``.get(`` forms are found outside the allowlist.
    """
    offenders: list[str] = []

    for py_file in sorted(_TESTS_ROOT.rglob("*.py")):

        # Skip allowlisted files — they contain the pattern intentionally.
        if py_file.resolve() in _ALLOWLIST:
            continue

        for lineno, line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if _PATTERN.search(line):
                offenders.append(f"{py_file}:{lineno}: {line.rstrip()}")

    assert offenders == [], (
        "Bare state[\"positions\"] / state[\"cash\"] / state[\"thesis\"] "
        "subscript or .get() found in tests/ outside the allowlist.\n"
        "These seeds are silent-regression fixtures — the live pipeline reads "
        "user:positions / user:thesis instead.  Migrate them to the canonical "
        "user:-prefixed keys, or (for genuine negative-controls) add to the "
        "allowlist with a documented reason.\n\nOffenders:\n"
        + "\n".join(offenders)
    )
