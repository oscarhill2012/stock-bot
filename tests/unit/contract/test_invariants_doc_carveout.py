"""Doc-presence guard for the A2.4 in-tick callback carve-out.

The carve-out clause in ``contract-invariants.md`` §C-Rule 1 makes the
direct-mutation write in ``_strategist_validation_callback`` conformant.
This test asserts the clause is present so future spec edits cannot
silently drop it.
"""
from __future__ import annotations

from pathlib import Path

# Resolve project root from this file's location — go up four levels:
# tests/unit/contract/test_invariants_doc_carveout.py -> project root.
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_INVARIANTS  = _PROJECT_ROOT / "docs" / "contract-invariants.md"
_AUDIT       = _PROJECT_ROOT / "docs" / "Phase8-contract-audit-fixes" / "contract-audit.md"


def test_invariants_carveout_clause_present() -> None:
    """The in-tick callback carve-out must be documented in §C-Rule 1."""
    text = _INVARIANTS.read_text(encoding="utf-8")
    assert "In-tick callback carve-out" in text, (
        "contract-invariants.md §C-Rule 1 is missing the in-tick callback "
        "carve-out clause added by A2.4."
    )


def test_audit_marks_383_as_conformant_under_carveout() -> None:
    """contract-audit.md §C-Rule 1 row for :383 must reference the carve-out."""
    text = _AUDIT.read_text(encoding="utf-8")
    assert "in-tick carve-out" in text.lower(), (
        "contract-audit.md does not mark the strategist/agent.py:383 row "
        "as conformant under the in-tick carve-out."
    )
