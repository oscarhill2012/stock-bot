"""Per-ticker analysts honour ``config/analysts.json::output_caps``.

After the 2026-05-25 schema split the contract surface changed:

1. **Prompt-facing** — the prose-cap values (``report_summary_max_chars`` and
   ``report_driver_body_max_chars``) from ``config/analysts.json`` are
   substituted into the rendered single-ticker instruction string.  The
   previous ``verdict_rationale_max_chars`` substitution was removed
   because ``rationale`` is no longer on the LLM emit-schema — Vertex's
   constrained decoder treated ``maxLength`` as a fill target.  If a future
   prompt rewrite bypasses the ``out_caps`` substitution path the LLM
   receives no explicit budget and the invariant silently breaks.

2. **Schema-facing** — the per-ticker LlmAgent's ``output_schema`` is
   ``LlmTickerVerdict`` (the narrow LLM emit-class).  Two structural
   guarantees flow from that: ``is_no_data`` and ``report`` are required
   (no defaults, no Optional), and ``extra="forbid"`` rejects drift
   between the prompt and the schema.  Substituting any other class would
   silently re-open the failure mode the split was designed to close.

Both paths are required: the prompt-side caps nudge the LLM; the
schema-side class enforces the required-fields contract on the validated
output.
"""
from __future__ import annotations

from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.per_ticker import build_news_branch_for_ticker
from config.analysts import get_analysts_config
from contract.evidence import LlmTickerVerdict

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

def test_news_per_ticker_prompt_contains_config_prose_caps():
    """Rendered News instruction substitutes the configured prose caps.

    The post-split prose budget is expressed via two caps on the
    ``AnalystReport`` block — ``report_summary_max_chars`` and
    ``report_driver_body_max_chars``.  Both flow from ``output_caps`` in
    ``config/analysts.json`` through ``build_news_instruction``.  If a
    future prompt rewrite drops either substitution, the LLM receives no
    explicit prose budget and the config-driven invariant silently breaks.
    """
    h = load_heuristics()
    branch = build_news_branch_for_ticker("AAPL", h.news_vocabulary)
    llm = _walk_to_llm_agent(branch)

    out_caps = get_analysts_config().output_caps

    assert str(out_caps.report_summary_max_chars) in llm.instruction, (
        "News per-ticker instruction does not carry the configured "
        "report_summary_max_chars value — the output_caps config path is "
        "broken; check build_news_instruction()."
    )

    assert str(out_caps.report_driver_body_max_chars) in llm.instruction, (
        "News per-ticker instruction does not carry the configured "
        "report_driver_body_max_chars value — the output_caps config path "
        "is broken; check build_news_instruction()."
    )


def test_fundamental_per_ticker_prompt_contains_config_prose_caps():
    """Mirror of the news test — Fundamental instruction must also carry both caps."""

    h = load_heuristics()
    branch = build_fundamental_branch_for_ticker("AAPL", h.fundamental_vocabulary)
    llm = _walk_to_llm_agent(branch)

    out_caps = get_analysts_config().output_caps

    assert str(out_caps.report_summary_max_chars) in llm.instruction, (
        "Fundamental per-ticker instruction does not carry the configured "
        "report_summary_max_chars value — check build_fundamental_instruction()."
    )

    assert str(out_caps.report_driver_body_max_chars) in llm.instruction, (
        "Fundamental per-ticker instruction does not carry the configured "
        "report_driver_body_max_chars value — check build_fundamental_instruction()."
    )


# ---------------------------------------------------------------------------
# Schema-side guard
# ---------------------------------------------------------------------------

def test_per_ticker_output_schema_is_llm_ticker_verdict():
    """`output_schema` on both per-ticker LlmAgents must be ``LlmTickerVerdict``.

    The narrow emit-class encodes the three structural fixes from the
    2026-05-25 schema split:

    1. ``is_no_data`` and ``report`` are required (no defaults, no Optional)
       — the JSON-Schema sent to Vertex marks them as mandatory, closing the
       dominant "decoder takes the shortest legal path" failure mode.
    2. ``extra="forbid"`` — drift between the prompt and the schema (e.g.
       a re-introduced ``rationale`` emit) fails loudly rather than silently
       dropping fields.
    3. ``model_validator`` rejects an empty ``ticker`` string that would
       otherwise silently break the joiner's per-ticker indexing.

    Substituting any other class for ``output_schema`` would re-open all
    three regressions, so we pin the concrete type here.
    """
    h = load_heuristics()

    branches = {
        "news":        build_news_branch_for_ticker("AAPL", h.news_vocabulary),
        "fundamental": build_fundamental_branch_for_ticker("AAPL", h.fundamental_vocabulary),
    }

    for analyst_name, branch in branches.items():
        llm = _walk_to_llm_agent(branch)

        assert llm.output_schema is LlmTickerVerdict, (
            f"{analyst_name} per-ticker LlmAgent ({llm.name!r}) uses "
            f"{llm.output_schema!r} instead of LlmTickerVerdict — the "
            f"required-fields contract is no longer enforced at the schema "
            f"level.  Update the per-ticker factory if the emit-schema has "
            f"legitimately changed."
        )
