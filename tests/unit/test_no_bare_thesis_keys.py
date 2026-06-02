"""Regression guards against bare state-key residue (audit findings A-086, A-014).

Three kinds of test:

1. ``test_no_bare_thesis_state_key_in_src`` — static scan: no subscript or
   ``.get()`` access to the bare ``thesis`` state key anywhere in ``src/``.

2. ``test_no_bare_positions_or_cash_state_keys_in_src`` — static scan: no
   subscript or ``.get()`` access to the bare ``positions`` or ``cash`` state
   keys anywhere in ``src/``.  The canonical persisted book is
   ``state["user:positions"]``, written solely by
   ``_executor_thesis_writer_callback`` in ``agents/executor/agent.py``.

3. ``test_strategist_state_delta_carries_no_bare_thesis_key`` — behavioural
   guard: run the ``StrategistContextShim`` end-to-end and assert the emitted
   ``state_delta`` never contains a bare ``thesis`` key, even when
   ``user:thesis`` is populated.

The static guards deliberately use regex on source content, not imports, so
they fire even if the offending code is unreachable at runtime.  A broad
``"thesis":`` dict-key regex is intentionally NOT used for the behavioural
guard (Change 3) because it would false-positive on legitimate non-state
fields such as ``decision_logger.py``'s snapshot ``"thesis": decision.get(...)``
and the JSON example block in ``prompts.py``.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agents.strategist.context_shim import StrategistContextShim

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
    # tests/unit/test_no_bare_thesis_keys.py -> tests/unit/ -> tests/ -> project root
    src_root = Path(__file__).parent.parent.parent / "src"
    for py_file in sorted(src_root.rglob("*.py")):
        for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
            if compiled.search(line):
                offenders.append(f"{py_file}:{lineno}: {line.rstrip()}")

    return offenders


# ---------------------------------------------------------------------------
# 1. Static guard — no bare thesis subscript / .get() in src/
# ---------------------------------------------------------------------------

def test_no_bare_thesis_state_key_in_src() -> None:
    """A-086: no ``state["thesis"]`` or ``state.get("thesis"...)`` anywhere in src/.

    The canonical cross-tick thesis lives at ``state["user:thesis"]``, written
    by ``_executor_thesis_writer_callback`` in ``agents/executor/agent.py``.
    The strategist prompt reads it via the ``{user:thesis?}`` placeholder.
    Any bare-key access is a regression.
    """
    pattern = r"""state\[\s*["']thesis["']\s*\]|state\.get\(\s*["']thesis["']"""
    offenders = _scan_src(pattern)

    assert offenders == [], (
        "Bare state['thesis'] / state.get('thesis') found in src/ — "
        "use state['user:thesis'] instead:\n"
        + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# 2. Static guard — no bare positions / cash subscript / .get() in src/
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


# ---------------------------------------------------------------------------
# 3. Behavioural guard — state_delta from StrategistContextShim carries no
#    bare thesis key, even with user:thesis populated.
# ---------------------------------------------------------------------------

@pytest.fixture
def _warm_state() -> dict:
    """Minimal session state with ``user:thesis`` populated (warm-start tick).

    Provides all keys the shim reads so it can complete without error.
    """
    from datetime import UTC, datetime

    return {
        "tickers":              ["AAPL"],
        "tick_id":              "guard-tick-1",
        "as_of":                datetime(2026, 5, 20, 13, 30, tzinfo=UTC),
        "user:positions":       {},
        "portfolio":            {"cash": 100_000.0, "positions": {}},
        "technical_evidence":   [],
        "fundamental_evidence": [],
        "news_evidence":        [],
        "smart_money_evidence": [],
        # Warm-start: thesis is populated so the bridge temptation is strong.
        "user:thesis":          "AAPL momentum — target $225",
    }


def test_strategist_state_delta_carries_no_bare_thesis_key(_warm_state: dict) -> None:
    """A-086 behavioural guard: StrategistContextShim must not emit bare 'thesis'.

    The strategist prompt uses ``{user:thesis?}``; ADK resolves it from
    ``state["user:thesis"]`` without any shim bridge.  If the bridge is ever
    re-introduced, this test will catch it at the unit level.
    """
    shim = StrategistContextShim()

    fake_session = MagicMock()
    fake_session.state = _warm_state
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-guard"
    fake_ctx.session = fake_ctx.session_service = fake_session

    async def _drain() -> list:
        events: list = []
        async for ev in shim._run_async_impl(fake_ctx):
            events.append(ev)
        return events

    events = asyncio.run(_drain())
    assert len(events) == 1, f"Shim must yield exactly one event; got {len(events)}"

    delta = events[0].actions.state_delta
    assert "thesis" not in delta, (
        "StrategistContextShim emitted a bare 'thesis' key in state_delta — "
        "this re-introduces the legacy bridge; the prompt must use {user:thesis?} "
        "resolved by ADK from state['user:thesis'] directly."
    )


# ---------------------------------------------------------------------------
# 4. Positive proof — prompts.py uses {user:thesis?}, not {thesis}
# ---------------------------------------------------------------------------

def test_strategist_prompt_resolves_user_thesis_placeholder() -> None:
    """A-086 positive proof: strategist prompt uses {user:thesis?}, not {thesis}.

    After the rename:
    - The bare ``{thesis}`` placeholder must NOT appear in the instruction.
    - The optional ``{user:thesis?}`` placeholder MUST appear.

    This guards against the placeholder being silently reverted in a future
    prompt edit.
    """
    from agents.strategist.prompts import STRATEGIST_INSTRUCTION

    assert "{thesis}" not in STRATEGIST_INSTRUCTION, (
        "Bare {thesis} placeholder found in STRATEGIST_INSTRUCTION — "
        "it should be {user:thesis?} so ADK resolves from state['user:thesis']"
    )
    assert "{user:thesis?}" in STRATEGIST_INSTRUCTION, (
        "Optional {user:thesis?} placeholder missing from STRATEGIST_INSTRUCTION — "
        "the strategist prompt must read the user-scoped thesis via this placeholder"
    )
