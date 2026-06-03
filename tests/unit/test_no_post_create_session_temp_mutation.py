# tests/unit/test_no_post_create_session_temp_mutation.py
"""Lint: no module may mutate ``state["temp:_…"]`` after calling
``create_session`` — A-010 / A-047 regression guard.

ADK strips ``temp:``-prefixed keys at persistence time, and the runner
re-fetches the session for every invocation.  Any post-``create_session``
mutation onto a ``temp:`` key is therefore silently discarded.  The only
sanctioned install path is :class:`HandleInjectorPlugin`'s
``before_run_callback``.

The lint walks every ``.py`` file under ``src/`` and ``scripts/`` and:
1. Skips files that do not call ``create_session``.
2. In files that do, searches for ``Subscript`` assignments whose key
   is a string literal starting with ``"temp:"``.  Any such assignment
   that appears in source order *after* the first ``create_session``
   call is a lint failure.

This catches the trace_tick.py-style bug (Plan 01 deleted that file,
but the lint must keep landing).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Project roots — both source and scripts. tests/ is excluded; fixtures
# may legitimately mutate temp: state to set up an arrange step.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (PROJECT_ROOT / "src", PROJECT_ROOT / "scripts")


def _calls_create_session(tree: ast.AST) -> list[int]:
    """Return line numbers of every ``create_session`` call in ``tree``."""

    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name: str | None = None
            if isinstance(fn, ast.Attribute):
                name = fn.attr
            elif isinstance(fn, ast.Name):
                name = fn.id
            if name == "create_session":
                lines.append(node.lineno)
    return sorted(lines)


def _temp_key_assignments(tree: ast.AST) -> list[tuple[int, str]]:
    """Return ``(lineno, key)`` for every ``something["temp:…"] = …`` assignment."""

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        # Catches both ``state["temp:_x"] = y`` (Assign) and augmented
        # forms; we keep it simple and only check plain Assign.
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant):
                    key = target.slice.value
                    if isinstance(key, str) and key.startswith("temp:"):
                        out.append((node.lineno, key))
    return out


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return files


@pytest.mark.parametrize("path", _iter_py_files(), ids=lambda p: str(p.relative_to(PROJECT_ROOT)))
def test_no_temp_assignment_after_create_session(path: Path) -> None:
    """No `temp:`-prefixed assignment may follow a ``create_session`` call
    in the same module.  The sanctioned install path is
    ``HandleInjectorPlugin.before_run_callback``."""

    # HandleInjectorPlugin is the *one* module allowed to assign to
    # ``state["temp:_…"]`` — and it does so inside before_run_callback,
    # never after a create_session call (it doesn't call create_session
    # at all).  Exclude it explicitly so the lint doesn't flag itself.
    if path.name == "handle_injector_plugin.py":
        return

    src = path.read_text()
    tree = ast.parse(src, filename=str(path))

    cs_lines = _calls_create_session(tree)
    if not cs_lines:
        return  # No create_session in this file → nothing to guard.

    first_cs_line = cs_lines[0]
    offenders = [
        (ln, key) for (ln, key) in _temp_key_assignments(tree) if ln > first_cs_line
    ]

    assert not offenders, (
        f"{path.relative_to(PROJECT_ROOT)} mutates temp:-prefixed state "
        f"after create_session (line {first_cs_line}); ADK silently "
        f"discards these. Use HandleInjectorPlugin instead. "
        f"Offending assignments: {offenders}"
    )
