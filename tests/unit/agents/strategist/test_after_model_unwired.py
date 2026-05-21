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
        # branch is SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent]].
        # The LlmAgent is at sub_agents[1].inner — the retry wrapper around
        # it is structural, not behavioural, so the after-model callback
        # check still applies to the underlying LlmAgent.  Wrapping the
        # LlmAgent (rather than the whole SequentialAgent) is required so
        # ContextShim's state_delta event reaches the ADK Runner; see
        # ``build_strategist``'s docstring.
        llm = branch.sub_agents[1].inner
        assert llm.after_model_callback is None, (
            "Strategist LlmAgent has after_model_callback wired in default env "
            "(STOCKBOT_TRACE not set) — that should be None."
        )


def test_strategist_module_does_not_expose_clamp_or_singleton() -> None:
    """The strategist module must not expose the deleted clamp symbols and
    must not expose a module-level ``strategist_agent`` singleton.

    Two cleanups overlap on this guard:

    * **A2.3** deleted the ``_strategist_after_model_composite`` clamp chain
      because the prompt forbids negative weights and callbacks are Rule 3-
      forbidden from yielding state.  Future LLM drift fixes belong in a
      fresh ``output_schema`` validator, not a re-attached clamp.
    * **2026-05-21 model-config refactor** deleted the module-level
      ``strategist_agent`` singleton in favour of the
      :func:`build_strategist` factory so the model ID lives in
      ``config/models.json`` and no shadow LlmAgent is built at import
      time.  See the module docstring of ``agents.strategist.agent`` for
      the full rationale.

    If a future engineer re-introduces *any* of these symbols, this test
    fails fast.
    """
    from agents.strategist import agent as sa

    # Clamp symbols deleted in A2.3.
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

    # Module-level singleton deleted 2026-05-21 — production wiring now
    # goes through ``build_strategist`` so the model ID is read from
    # ``config/models.json`` rather than baked in at import time.
    assert not hasattr(sa, "strategist_agent"), (
        "agents.strategist.agent.strategist_agent should be gone — "
        "production wires via build_strategist() (2026-05-21 refactor)."
    )
