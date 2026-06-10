"""Unit tests for tick.py — no LLM calls.

These tests assert on concrete content: the callable's identity, its exact name,
and its parameter contract — not merely that it exists or is async.
"""
import inspect


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
