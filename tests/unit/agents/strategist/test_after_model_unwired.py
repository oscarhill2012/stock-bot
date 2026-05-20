"""Guard test — the production Strategist must not wire any
``after_model_callback`` except the optional trace hook gated on
``STOCKBOT_TRACE=1``.

Why: the legacy ``_strategist_after_model_composite`` chained a stance-
bounds clamp (against Gemini negative-weight drift) with a trace
callback.  Production wiring (``orchestrator/pipeline.py:_build_strategist``)
was already trace-only, so the clamp never actually ran in production.
The Strategist prompt forbids negative weights; the clamp was YAGNI
defensive code.  This test pins the contract — if anyone re-attaches the
composite, this fails fast.

Also asserts the clamp symbols are *gone* (not merely unwired) so a
future engineer cannot quietly re-import them.  If LLM drift recurs, the
fix is a fresh ``output_schema`` validator on ``TickerStance``, not a
re-attached after-model callback (callbacks are Rule 3-forbidden from
yielding state).
"""
from __future__ import annotations

import os
from unittest.mock import patch


def test_pipeline_strategist_branch_has_no_after_model_callback_by_default() -> None:
    """The production strategist LlmAgent must have ``after_model_callback=None``
    unless ``STOCKBOT_TRACE=1``.

    Inspects the SequentialAgent returned by ``_build_strategist`` and
    asserts the inner LlmAgent has no after-model callback in the default
    (non-trace) environment.
    """
    # Ensure trace is OFF for this assertion.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("STOCKBOT_TRACE", None)
        from orchestrator.pipeline import _build_strategist

        branch = _build_strategist()
        # branch is SequentialAgent[ContextShim, LlmAgent]; LlmAgent is the
        # second child.
        llm = branch.sub_agents[1]
        assert llm.after_model_callback is None, (
            "Strategist LlmAgent has after_model_callback wired in default env "
            "(STOCKBOT_TRACE not set) — that should be None."
        )


def test_module_singleton_no_longer_wires_after_model_composite() -> None:
    """The strategist module singleton must not wire
    ``_strategist_after_model_composite`` (it no longer exists), and the
    clamp symbols it chained must also be gone.
    """
    from agents.strategist import agent as sa

    # All four symbols delete as part of A2.3.
    assert not hasattr(sa, "_strategist_after_model_composite"), (
        "_strategist_after_model_composite should be removed in A2.3."
    )
    assert not hasattr(sa, "_clamp_stance_bounds_after_model"), (
        "_clamp_stance_bounds_after_model should be removed in A2.3 "
        "(prompt forbids negative weights; clamp was unwired YAGNI)."
    )
    assert not hasattr(sa, "_CLAMPED_STANCE_FIELDS"), (
        "_CLAMPED_STANCE_FIELDS should be removed in A2.3 — it was only "
        "consumed by the deleted clamp."
    )
    # The module-level singleton must not pass after_model_callback.
    assert sa.strategist_agent.after_model_callback is None, (
        "strategist_agent singleton still has after_model_callback wired; "
        "A2.3 unwires it."
    )
