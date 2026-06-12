"""Unit tests for the ``_drain_runner_events`` helper in ``orchestrator.tick``.

Covers A-081: operator interrupts (KeyboardInterrupt, SystemExit) must
propagate out of the event drain rather than being silently swallowed,
while ADK 1.32 teardown noise (BaseExceptionGroup wrapping GeneratorExit,
and AttributeError on NoneType) must continue to be absorbed.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from orchestrator.tick import _drain_runner_events


# ---------------------------------------------------------------------------
# Helper — async iterator that yields one event then raises an exception
# ---------------------------------------------------------------------------

async def _raising_aiter(exc):
    """An async iterator that yields one event then raises ``exc``.

    Used to simulate the ADK runner's event stream firing an exception
    partway through draining — matching the real failure mode where the
    teardown error fires *after* at least one event has been consumed.

    :param exc: The exception instance to raise after the first yield.
    :returns: An async generator.
    """
    async def _gen():
        yield object()  # one normal event before the exception
        raise exc

    return _gen()


# ---------------------------------------------------------------------------
# Propagation tests — these must RAISE
# ---------------------------------------------------------------------------

def test_drain_propagates_keyboard_interrupt():
    """A Ctrl-C during the drain must propagate, not be swallowed (A-081).

    KeyboardInterrupt is a BaseException, not an Exception — a plain
    ``except Exception`` would silently miss it.  The helper must explicitly
    re-raise it so an operator Ctrl-C stops the run.
    """
    async def _run():
        events = await _raising_aiter(KeyboardInterrupt())
        await _drain_runner_events(events, tick_id="t1")

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_run())


def test_drain_propagates_system_exit():
    """SystemExit (process shutdown) must propagate too.

    SystemExit is also a BaseException.  It must escape the drain so a
    clean shutdown signal (e.g. from a process manager) is not eaten.
    """
    async def _run():
        events = await _raising_aiter(SystemExit())
        await _drain_runner_events(events, tick_id="t1")

    with pytest.raises(SystemExit):
        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Swallow tests — these must NOT raise, and must emit a warning log
# ---------------------------------------------------------------------------

def test_drain_swallows_adk_teardown_baseexceptiongroup(caplog):
    """A BaseExceptionGroup wrapping GeneratorExit (ADK teardown) is swallowed + logged.

    This is the primary ADK 1.32 teardown noise shape.  GeneratorExit is
    a BaseException (not Exception), so the swallow requires ``except
    BaseException`` — but KeyboardInterrupt/SystemExit must still escape,
    hence the explicit re-raise guard.
    """
    grp = BaseExceptionGroup("teardown", [GeneratorExit()])

    async def _run():
        events = await _raising_aiter(grp)
        await _drain_runner_events(events, tick_id="t9")

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())   # must NOT raise

    assert any(
        "ADK runner raised during tick t9" in r.message for r in caplog.records
    ), "Expected a warning log mentioning tick t9"


def test_drain_swallows_attribute_error(caplog):
    """The other known ADK teardown shape (AttributeError on NoneType) is swallowed.

    ADK 1.32 may raise ``AttributeError("'NoneType' object has no attribute
    'partial'")`` after the pipeline completes.  This must be absorbed and
    logged, not propagated to the caller.
    """
    async def _run():
        events = await _raising_aiter(
            AttributeError("'NoneType' object has no attribute 'partial'")
        )
        await _drain_runner_events(events, tick_id="t2")

    with caplog.at_level(logging.WARNING):
        asyncio.run(_run())

    assert any(
        "t2" in r.message for r in caplog.records
    ), "Expected a warning log mentioning tick t2"
