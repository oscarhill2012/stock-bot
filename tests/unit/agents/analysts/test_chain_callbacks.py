"""Regression tests for ``_chain_before`` / ``_chain_after`` in ``_common.py``.

ADK invokes ``before_model_callback`` and ``after_model_callback`` with
**keyword arguments** (``callback_context=...``, ``llm_request=...`` or
``llm_response=...``).  The outer wrapper returned by ``_chain_*`` must
therefore accept those exact keyword names — if its parameters are renamed
(e.g. to ``ctx``), every live LLM tick raises
``TypeError: unexpected keyword argument 'callback_context'`` and the
analyst pipeline collapses.

These tests pin the contract so it cannot silently regress.
"""
from __future__ import annotations

from agents.analysts._common import _chain_after, _chain_before

# ---------------------------------------------------------------------------
# _chain_before
# ---------------------------------------------------------------------------

def test_chain_before_accepts_adk_keyword_arguments():
    """The chained wrapper must be callable with ADK's exact kwarg names.

    Mirrors how ``base_llm_flow._handle_before_model_callback`` invokes the
    callback: ``callback(callback_context=..., llm_request=...)``.
    """
    captured: dict = {}

    def inner(ctx, req):
        """Capture both positional args so we can assert pass-through ordering."""
        captured["ctx"] = ctx
        captured["req"] = req
        return None

    chained = _chain_before(inner)
    # Invoke with the **kwargs** ADK uses — this is the regression surface.
    result = chained(callback_context="CTX", llm_request="REQ")

    assert result is None
    assert captured == {"ctx": "CTX", "req": "REQ"}


def test_chain_before_short_circuits_on_first_non_none():
    """First callback to return a non-None value wins; later callbacks are skipped."""
    calls: list[str] = []

    def first(ctx, req):
        """Record invocation and short-circuit the chain by returning a sentinel."""
        calls.append("first")
        return "HIT"

    def second(ctx, req):
        """Should never run because ``first`` short-circuited."""
        calls.append("second")
        return None

    chained = _chain_before(first, second)
    assert chained(callback_context=None, llm_request=None) == "HIT"
    assert calls == ["first"]


def test_chain_before_empty_returns_none():
    """An empty chain (or all-``None`` entries) collapses to ``None``."""
    assert _chain_before() is None
    assert _chain_before(None, None) is None


# ---------------------------------------------------------------------------
# _chain_after
# ---------------------------------------------------------------------------

def test_chain_after_accepts_adk_keyword_arguments():
    """Same kwarg-name contract as the before chain, but for after-model hooks."""
    captured: dict = {}

    def inner(ctx, resp):
        """Capture pass-through args so the test can assert ordering."""
        captured["ctx"] = ctx
        captured["resp"] = resp

    chained = _chain_after(inner)
    chained(callback_context="CTX", llm_response="RESP")

    assert captured == {"ctx": "CTX", "resp": "RESP"}


def test_chain_after_runs_every_callback():
    """After-model callbacks are side-effect only; all of them must fire."""
    calls: list[str] = []

    def one(ctx, resp):
        """First sink — records that it ran."""
        calls.append("one")

    def two(ctx, resp):
        """Second sink — also records that it ran (no short-circuit)."""
        calls.append("two")

    chained = _chain_after(one, two)
    chained(callback_context=None, llm_response=None)
    assert calls == ["one", "two"]


def test_chain_after_empty_returns_none():
    """Empty after-chain collapses to ``None`` so ADK skips the callback slot."""
    assert _chain_after() is None
    assert _chain_after(None, None) is None
