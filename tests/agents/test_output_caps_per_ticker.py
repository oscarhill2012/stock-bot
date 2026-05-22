"""Per-ticker analysts honour ``config/analysts.json::output_caps``.

Two assertions per analyst:

1. **Prompt-facing** — the literal ``verdict_rationale_max_chars`` value from
   ``config/analysts.json`` is substituted into the rendered single-ticker
   instruction string.  If a future prompt rewrite bypasses the
   ``out_caps`` substitution path, the LLM receives no explicit cap and
   the invariant silently breaks.

2. **Schema-facing** — the per-ticker LlmAgent's ``output_schema`` is
   ``TickerVerdict`` (or a subclass) and therefore inherits the Pydantic
   ``max_length`` enforced on the ``rationale`` field in ``AnalystVerdict``.
   The schema-side cap survives as long as no task accidentally substitutes
   an ad-hoc schema that does not extend ``AnalystVerdict``.

Both paths are required: the prompt-side cap nudges the LLM; the schema-
side cap enforces the contract on the validated output.
"""
from __future__ import annotations

from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.per_ticker import build_news_branch_for_ticker
from config.analysts import get_analysts_config
from contract.evidence import AnalystVerdict, TickerVerdict

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _walk_to_llm_agent(branch):
    """Return the inner ``LlmAgent`` regardless of wrapper nesting depth.

    The per-ticker branch is typically structured as::

        IsolatedFailureWrapper
            └─ RetryingAgentWrapper
                   └─ LlmAgent

    This helper descends via ``inner`` until it finds an object that has
    both ``instruction`` and ``output_schema`` attributes — the defining
    shape of an ``LlmAgent``.

    Args:
        branch: The outermost agent returned by a per-ticker branch factory.

    Returns:
        The innermost ``LlmAgent`` instance.

    Raises:
        AssertionError: if no ``LlmAgent`` is found in the chain.
    """
    cur = branch
    while not (hasattr(cur, "instruction") and hasattr(cur, "output_schema")):
        cur = getattr(cur, "inner", None)
        assert cur is not None, (
            f"Could not locate an LlmAgent inside branch {branch!r}. "
            "Wrapper chain may have changed — update _walk_to_llm_agent."
        )
    return cur


# ---------------------------------------------------------------------------
# Prompt-side cap tests
# ---------------------------------------------------------------------------

def test_news_per_ticker_prompt_contains_config_rationale_cap():
    """Rendered News instruction substitutes ``verdict_rationale_max_chars``.

    Asserts that the string representation of the configured cap appears
    somewhere in the rendered single-ticker news instruction so the LLM
    knows the character budget for the ``rationale`` field.
    """
    h = load_heuristics()
    branch = build_news_branch_for_ticker("AAPL", h.news_vocabulary)
    llm = _walk_to_llm_agent(branch)

    # H4 (Spec A): the prompt now carries the *derived* prompt budget
    # (verdict_rationale_prompt_budget = max_chars − headroom), not the raw
    # schema cap.  Assert the budget value, not verdict_rationale_max_chars.
    cap = get_analysts_config().output_caps.verdict_rationale_prompt_budget

    assert str(cap) in llm.instruction, (
        f"News per-ticker instruction does not carry the configured rationale "
        f"prompt budget ({cap} chars).  The output_caps config path is broken — check "
        f"build_news_instruction() in src/agents/analysts/news/prompts.py."
    )


def test_fundamental_per_ticker_prompt_contains_config_rationale_cap():
    """Mirror of the news test — Fundamental instruction must also carry the cap."""

    h = load_heuristics()
    branch = build_fundamental_branch_for_ticker("AAPL", h.fundamental_vocabulary)
    llm = _walk_to_llm_agent(branch)

    # H4 (Spec A): the prompt now carries the *derived* prompt budget
    # (verdict_rationale_prompt_budget = max_chars − headroom), not the raw
    # schema cap.  Assert the budget value, not verdict_rationale_max_chars.
    cap = get_analysts_config().output_caps.verdict_rationale_prompt_budget

    assert str(cap) in llm.instruction, (
        f"Fundamental per-ticker instruction does not carry the configured "
        f"rationale prompt budget ({cap} chars).  Check "
        f"build_fundamental_instruction() in "
        f"src/agents/analysts/fundamental/prompts.py."
    )


# ---------------------------------------------------------------------------
# Schema-side cap tests
# ---------------------------------------------------------------------------

def test_per_ticker_output_schema_inherits_analyst_verdict_caps():
    """`output_schema` on both per-ticker LlmAgents must be ``TickerVerdict``.

    ``TickerVerdict`` inherits from ``AnalystVerdict``, which carries Pydantic
    ``max_length`` constraints on the ``rationale`` field.  Substituting any
    other schema would silently drop those constraints.

    Checks:
    - ``output_schema`` is a subclass of ``AnalystVerdict`` (inheritance guard).
    - ``output_schema is TickerVerdict`` (concrete type guard — no ad-hoc variant).
    """
    h = load_heuristics()

    branches = {
        "news":        build_news_branch_for_ticker("AAPL", h.news_vocabulary),
        "fundamental": build_fundamental_branch_for_ticker("AAPL", h.fundamental_vocabulary),
    }

    for analyst_name, branch in branches.items():
        llm = _walk_to_llm_agent(branch)

        # Guard 1: must extend AnalystVerdict so Pydantic enforces field caps.
        assert issubclass(llm.output_schema, AnalystVerdict), (
            f"{analyst_name} per-ticker LlmAgent ({llm.name!r}) bypassed "
            f"AnalystVerdict — schema-side output caps are no longer enforced.  "
            f"Got: {llm.output_schema!r}"
        )

        # Guard 2: must be exactly TickerVerdict (the canonical per-ticker type).
        assert llm.output_schema is TickerVerdict, (
            f"{analyst_name} per-ticker LlmAgent ({llm.name!r}) uses "
            f"{llm.output_schema!r} instead of TickerVerdict — update the "
            f"per-ticker factory if the schema type has legitimately changed."
        )
