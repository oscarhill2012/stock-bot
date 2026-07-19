"""Regression tests for ``_strip_forwarded_contents`` in ``agents.strategist.agent``.

Root cause (confirmed against ADK source, not re-litigated here): the
Strategist ``LlmAgent`` sets ``include_contents="none"`` to stop upstream
analyst outputs leaking into its prompt as conversation history — but that
is necessary-yet-insufficient.  Under ``include_contents="none"``, ADK's
``google/adk/flows/llm_flows/contents.py::_get_current_turn_contents`` still
scans session events backwards for the latest "turn-starter" and, via
``_is_other_agent_reply``, treats another agent's reply as a valid turn
start.  In the per-ticker fan-out pipeline, the last analyst branch to
finish is such a reply, so ``llm_request.contents`` ends up holding a
``role="user"`` ``Content`` shaped like:

    Part(text="For context:")
    Part(text='[FundamentalAnalyst_HRL] said: {"lean": "bearish", ...}')

This duplicates data the curated ``## Ticker Evidence`` section already
renders and arbitrarily over-weights one random ticker's analyst view.

``_strip_forwarded_contents`` is a module-level before-model callback (module
-level so it is importable here — the existing ``probe_before`` etc. are
nested inside ``build_strategist`` and cannot be imported) that empties
``llm_request.contents`` in place.  Emptying it is safe: ADK's own
``_get_current_turn_contents`` already returns ``[]`` when there is no
turn-starter, and the strategist's instruction reaches the model via the
separate system-instruction path, not via ``contents``.
"""
from __future__ import annotations

from google.adk.models import LlmRequest
from google.genai import types as genai_types

from agents.llm_retry import RetryingAgentWrapper
from agents.strategist.agent import _strip_forwarded_contents, build_strategist


def _make_leaked_request() -> LlmRequest:
    """Build a real ``LlmRequest`` whose ``.contents`` carries the captured
    leak shape — a ``role="user"`` Content wrapping "For context: [Agent]
    said: {json}" — matching the real leaked strategist request captured in
    ``backtests/long-baseline-2025/runs/post-plan3b-smoke/obs/logs/
    2025-09-22T13-30-00p00-00-open.json``.
    """
    leaked = genai_types.Content(
        role="user",
        parts=[
            genai_types.Part(text="For context:"),
            genai_types.Part(
                text='[FundamentalAnalyst_HRL] said: {"lean": "bearish", "confidence": 0.6}'
            ),
        ],
    )
    return LlmRequest(contents=[leaked])


# ---------------------------------------------------------------------------
# Core regression
# ---------------------------------------------------------------------------

def test_strip_forwarded_contents_empties_the_leaked_block():
    """After the callback runs, ``llm_request.contents`` must be ``[]``.

    This is the core regression: before ``_strip_forwarded_contents``
    existed, the leaked foreign-agent Content survived untouched.
    """
    req = _make_leaked_request()
    assert req.contents != []  # sanity: the fixture really does leak something

    _strip_forwarded_contents(callback_context=None, llm_request=req)

    assert req.contents == []


# ---------------------------------------------------------------------------
# Return-value contract
# ---------------------------------------------------------------------------

def test_strip_forwarded_contents_returns_none():
    """Must return ``None`` — a before-model callback returning non-``None``
    short-circuits the actual LLM call (see ``_chain_before`` semantics);
    we are mutating the request in place, not supplying a response."""
    req = _make_leaked_request()
    result = _strip_forwarded_contents(callback_context=None, llm_request=req)
    assert result is None


# ---------------------------------------------------------------------------
# ADK kwarg-name contract
# ---------------------------------------------------------------------------

def test_strip_forwarded_contents_accepts_adk_keyword_arguments():
    """ADK invokes before-model callbacks with ``callback_context=`` and
    ``llm_request=`` keyword arguments — the callback must accept those
    exact names or every live tick raises
    ``TypeError: unexpected keyword argument 'callback_context'``."""
    req = _make_leaked_request()
    # Invoke with kwargs, exactly as ADK's base_llm_flow does.
    result = _strip_forwarded_contents(callback_context="CTX", llm_request=req)
    assert result is None
    assert req.contents == []


# ---------------------------------------------------------------------------
# Wiring — the built agent must actually use this callback
# ---------------------------------------------------------------------------

def test_build_strategist_strips_forwarded_contents_in_before_model_chain():
    """The inner strategist ``LlmAgent``'s ``before_model_callback`` must
    strip a leaked foreign-agent Content when invoked with ADK's kwarg
    convention — proving the fix is actually wired into the built agent,
    not just unit-testable in isolation."""
    branch = build_strategist()

    retrying = branch.sub_agents[1]
    assert isinstance(retrying, RetryingAgentWrapper)

    llm = retrying.inner
    before_model_callback = llm.before_model_callback
    assert before_model_callback is not None

    req = _make_leaked_request()
    before_model_callback(callback_context=None, llm_request=req)

    assert req.contents == []
