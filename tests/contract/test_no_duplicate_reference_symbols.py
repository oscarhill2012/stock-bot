"""Tripwire — only ``data.reference_symbols`` may define ``REFERENCE_SYMBOLS``.

Catches regressions where a future engineer reintroduces a local tuple to
"avoid the import" — exactly what audit A-035 found three times.
"""
from __future__ import annotations

import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_only_one_reference_symbols_definition_in_repo():
    """Scan src/ and scripts/ for any *definition* of ``REFERENCE_SYMBOLS`` — the
    canonical module is the only allowed hit. Excludes tests/ (aliases allowed)."""

    # Match a definition/assignment only: the (optionally underscore-prefixed)
    # name at the start of a line, an optional type annotation, then ``=``.
    # Anchoring to the line start with re.MULTILINE deliberately excludes
    # ``for symbol in REFERENCE_SYMBOLS:`` loops and ``import REFERENCE_SYMBOLS``
    # statements, which are legitimate *uses*, not re-definitions. The audit
    # (A-035) found three ``_REFERENCE_SYMBOLS = ...`` definitions; the
    # underscore form is included so a reintroduced private copy is also caught.
    pattern = re.compile(r"^\s*_?REFERENCE_SYMBOLS\s*(?::[^=\n]+)?=", re.MULTILINE)

    hits: list[Path] = []
    for sub in ("src", "scripts"):
        for path in (_PROJECT_ROOT / sub).rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(path.relative_to(_PROJECT_ROOT))

    canonical = Path("src/data/reference_symbols.py")
    assert hits == [canonical], (
        f"REFERENCE_SYMBOLS must be defined only in {canonical}; "
        f"also found in: {[str(h) for h in hits if h != canonical]}"
    )
