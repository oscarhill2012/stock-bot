"""Regression guard against bare state-key residue (audit finding A-014).

This file used to be ``test_no_bare_thesis_keys.py`` and carried four tests:
three guarding the now-removed portfolio-level ``user:thesis`` string, plus
this one guarding the still-live ``user:positions`` / ``cash`` migration. The
C5 sweep retired the thesis-specific tests (C4 deleted ``user:thesis`` and
its ``{user:thesis?}`` prompt placeholder entirely, so there is nothing left
to guard against a bridge back to a bare ``thesis`` key) and renamed the file
to describe what remains.

``test_no_bare_positions_or_cash_state_keys_in_src`` — static scan: no
subscript or ``.get()`` access to the bare ``positions`` or ``cash`` state
keys anywhere in ``src/``.  The canonical persisted book is
``state["user:positions"]``, written solely by
``_executor_thesis_writer_callback`` in ``agents/executor/agent.py``.

The static guard deliberately uses regex on source content, not imports, so
it fires even if the offending code is unreachable at runtime.
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Helper — collect offending lines matching a pattern inside src/
# ---------------------------------------------------------------------------

def _scan_src(pattern: str) -> list[str]:
    """Return list of ``'path:lineno: line'`` strings for every match of
    ``pattern`` found in ``*.py`` files under ``src/``.

    ``src/`` is located relative to this file (``tests/unit/`` → project root
    → ``src/``), so the scan is correct regardless of the working directory
    from which pytest is invoked.

    Parameters
    ----------
    pattern:
        A compiled-ready regex string.  Matched per line (``re.search``).

    Returns
    -------
    list[str]
        One entry per matching line, formatted for readable assertion output.
    """
    compiled = re.compile(pattern)
    offenders: list[str] = []

    # Anchor to __file__ so the scan is cwd-independent.
    src_root = Path(__file__).parent.parent.parent / "src"
    py_files = sorted(src_root.rglob("*.py"))

    # Fail loudly if the scan root resolved wrong — an empty file list would
    # otherwise let this guard pass vacuously without scanning anything.
    assert py_files, (
        f"No Python files found under {src_root} — the scan root resolved "
        "incorrectly; this guard would otherwise pass without checking anything."
    )

    for py_file in py_files:
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if compiled.search(line):
                offenders.append(f"{py_file}:{lineno}: {line.rstrip()}")

    return offenders


# ---------------------------------------------------------------------------
# Static guard — no bare positions / cash subscript / .get() in src/
# ---------------------------------------------------------------------------

def test_no_bare_positions_or_cash_state_keys_in_src() -> None:
    """A-014: no bare ``state["positions"]`` / ``state["cash"]`` in src/.

    The canonical persisted book is ``state["user:positions"]``, written solely
    by ``_executor_thesis_writer_callback`` in ``agents/executor/agent.py``.
    Any bare ``state["positions"]`` or ``state["cash"]`` subscript / ``.get()``
    is a regression to the pre-migration naming.
    """
    pattern = (
        r"""state\[\s*["'](positions|cash)["']\s*\]"""
        r"""|state\.get\(\s*["'](positions|cash)["']"""
    )
    offenders = _scan_src(pattern)

    assert offenders == [], (
        "Bare state['positions'] / state['cash'] found in src/ — "
        "use state['user:positions'] instead:\n"
        + "\n".join(offenders)
    )
