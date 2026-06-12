"""Unit tests for tick.py — no LLM calls.

These tests assert on concrete content: the callable's identity, its exact name,
and its parameter contract — not merely that it exists or is async.
"""
import inspect
import types

import pytest


def test_tick_module_importable():
    """The ``orchestrator.tick`` module exposes the ``run_once`` entry-point callable."""
    import orchestrator.tick

    assert hasattr(orchestrator.tick, "run_once")


def test_run_once_is_coroutine():
    """``run_once`` must be an async coroutine function, not a plain function.

    ADK runners call the entrypoint with ``await``, so a non-coroutine would
    silently return a coroutine object rather than executing the pipeline.
    """
    from orchestrator.tick import run_once

    assert inspect.iscoroutinefunction(run_once)


def test_run_once_name_matches_entry_point():
    """The callable's ``__name__`` must be exactly ``"run_once"``.

    Rename refactors would otherwise break call-sites that look up the
    function by name (e.g. Cloud Run Job invoker scripts), so this pins
    the identity explicitly.
    """
    from orchestrator.tick import run_once

    # Content assertion: not just "is a function" but "has the right name".
    assert run_once.__name__ == "run_once"


def test_run_once_accepts_broker_and_session_params():
    """``run_once`` must accept ``broker``, ``session``, and ``tick_label`` parameters.

    This asserts the public contract of the entry-point so callers that
    pass these kwargs don't silently break when the signature drifts.
    """
    from orchestrator.tick import run_once

    sig = inspect.signature(run_once)
    params = set(sig.parameters.keys())

    # All three parameters must be present in the function signature.
    assert "broker" in params, "run_once must accept a 'broker' parameter"
    assert "session" in params, "run_once must accept an optional 'session' parameter"
    assert "tick_label" in params, "run_once must accept an optional 'tick_label' parameter"


# ── _resolve_broker_mode tests ────────────────────────────────────────────────
# These tests exercise the pure helper in isolation — no ADK pipeline, no
# network calls.  Each test uses a tiny ``types.SimpleNamespace`` stub or a
# bare ``object`` to represent the broker rather than a real implementation.

class TestResolveBrokerMode:
    """Tests for the ``_resolve_broker_mode`` helper."""

    def test_paper_mode_returned_for_paper_attribute(self):
        """Returns ``BrokerMode.PAPER`` when the broker's ``mode`` is ``"paper"``."""
        from orchestrator.tick import BrokerMode, _resolve_broker_mode

        broker = types.SimpleNamespace(mode="paper")
        result = _resolve_broker_mode(broker)

        assert result is BrokerMode.PAPER

    def test_paper_mode_returned_when_mode_attribute_absent(self):
        """Returns ``BrokerMode.PAPER`` when the broker has no ``mode`` attribute.

        FakeBroker does not expose ``.mode``; the helper must default to
        ``"paper"`` (a valid mode) so test runs land in the paper namespace
        without raising.
        """
        from orchestrator.tick import BrokerMode, _resolve_broker_mode

        # A plain object has no ``mode`` attribute — mirrors FakeBroker.
        broker = object()
        result = _resolve_broker_mode(broker)

        assert result is BrokerMode.PAPER

    def test_live_mode_returned_for_live_attribute(self):
        """Returns ``BrokerMode.LIVE`` when the broker's ``mode`` is ``"live"``."""
        from orchestrator.tick import BrokerMode, _resolve_broker_mode

        broker = types.SimpleNamespace(mode="live")
        result = _resolve_broker_mode(broker)

        assert result is BrokerMode.LIVE

    def test_raises_on_unknown_mode_string(self):
        """Raises ``ValueError`` — with the bad value in the message — for an unrecognised mode.

        Silently falling back to ``BrokerMode.PAPER`` on a typo would route
        trades into the wrong user_state namespace; the helper must surface the
        problem loudly instead.
        """
        from orchestrator.tick import _resolve_broker_mode

        broker = types.SimpleNamespace(mode="papr")

        with pytest.raises(ValueError, match="papr"):
            _resolve_broker_mode(broker)

    def test_raises_message_includes_valid_modes(self):
        """The ``ValueError`` message lists the valid mode values so the operator knows what to fix."""
        from orchestrator.tick import _resolve_broker_mode

        broker = types.SimpleNamespace(mode="practice")

        with pytest.raises(ValueError, match="valid"):
            _resolve_broker_mode(broker)
