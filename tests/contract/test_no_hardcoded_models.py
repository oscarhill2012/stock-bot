"""Contract test: no hardcoded LLM model literals in ``src/``.

The single source of truth for every LLM / embedding model ID is
``config/models.json``, loaded via :func:`src.config.models.get_models_config`.
Production code must never bake a model literal (e.g. ``"gemini-3.5-flash"``,
``"text-embedding-005"``) into a Python source string — doing so silently
shadows the central config and is exactly the failure mode that bit on
2026-05-20 (see ``docs/todo-fixes.md`` §3.8: a strategist model swap was
applied to one literal but not the parallel literal in ``pipeline.py``, so
production kept running the old model with no error).

This test walks the AST of every ``.py`` file under ``src/`` and flags any
string literal that matches the forbidden prefixes, with two exemptions:

1. **Docstrings** — module / class / function docstrings are documentation
   and routinely cite the model names they describe.  We detect docstrings
   by their AST position (first ``Expr`` statement of a ``Module`` /
   ``ClassDef`` / ``FunctionDef`` / ``AsyncFunctionDef`` body).
2. **``src/config/models.py``** — the loader file itself.  Its docstring
   and any inline string in the loader's body is allowed to mention model
   names verbatim because that is the file that *defines* the central
   contract.  In practice the loader doesn't contain any such literals,
   but exempting the file keeps the contract clear about who owns these
   strings.

If this test fires, the fix is **always** the same: read the value from
``get_models_config()`` instead of inlining the literal.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Forbidden prefixes — every Gemini and Vertex embedding model ID we use
# starts with one of these.  Add new families here when adopted.
FORBIDDEN_PREFIXES: tuple[str, ...] = (
    "gemini-",
    "text-embedding-",
)

# Files allowed to mention model literals verbatim outside docstrings.  The
# central loader is the only such file — see the module docstring above.
EXEMPT_FILES: frozenset[str] = frozenset({
    # Path relative to the ``src/`` root, POSIX style.
    "config/models.py",
})

# Project layout: this file lives at ``tests/contract/...``; ``src/`` is two
# levels up from the tests root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT     = PROJECT_ROOT / "src"


def _collect_docstring_node_ids(tree: ast.AST) -> set[int]:
    """Return the ``id()`` of every ``ast.Constant`` that is a docstring.

    A docstring is the first statement of a ``Module``, ``ClassDef``,
    ``FunctionDef``, or ``AsyncFunctionDef`` body when that statement is an
    ``ast.Expr`` wrapping an ``ast.Constant`` string.  We track node identity
    rather than position so the caller can cheaply check membership while
    walking the same tree.

    Parameters
    ----------
    tree:
        Parsed AST of one Python source file.

    Returns
    -------
    set[int]
        The set of ``id(constant_node)`` values for every docstring constant
        in the tree.  Empty if the file has no docstrings.
    """
    docstring_ids: set[int] = set()

    def _maybe_record(node: ast.AST) -> None:
        """Record the docstring constant (if any) of ``node``'s body."""
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstring_ids.add(id(first.value))

    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            _maybe_record(node)

    return docstring_ids


def _iter_source_files() -> list[Path]:
    """Yield every ``.py`` file under ``src/`` excluding ``__pycache__``.

    Sorted for deterministic test output so a future regression failure
    always reports offenders in the same order.
    """
    return sorted(
        p for p in SRC_ROOT.rglob("*.py") if "__pycache__" not in p.parts
    )


@pytest.mark.contract
def test_no_hardcoded_model_literals_in_src() -> None:
    """Fail loudly if any forbidden model literal appears in ``src/`` code.

    Walks every ``.py`` file under ``src/``, parses it, then scans every
    ``ast.Constant`` whose value is a string.  Skips:

      - docstring constants (detected by AST position),
      - files listed in :data:`EXEMPT_FILES`.

    Any remaining constant whose value starts with one of
    :data:`FORBIDDEN_PREFIXES` is collected as an offence.  All offences
    are reported in a single ``AssertionError`` so a regression sweep
    catches every site in one run rather than firing one-by-one.
    """
    offences: list[str] = []

    for path in _iter_source_files():
        rel = path.relative_to(SRC_ROOT).as_posix()
        if rel in EXEMPT_FILES:
            continue

        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        docstring_ids = _collect_docstring_node_ids(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if not isinstance(node.value, str):
                continue
            if id(node) in docstring_ids:
                continue
            if not any(node.value.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
                continue

            offences.append(
                f"  {rel}:{node.lineno}: hardcoded model literal "
                f"{node.value!r} — load via get_models_config() instead"
            )

    assert not offences, (
        "Hardcoded LLM/embedding model literals found in src/.  Every model "
        "ID must come from config/models.json via "
        "src.config.models.get_models_config().  See the docstring of this "
        "test for the 2026-05-20 incident that motivated this contract.\n\n"
        + "\n".join(offences)
    )
