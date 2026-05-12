"""Smart-money analyst LlmAgent — evidence-only output (D3).

Smart-money is the only analyst whose ``before_agent_callback`` can short-
circuit the LLM entirely when no material activity is detected across the
watchlist (see ``fetch.py``).  In that case ``state["smart_money_verdicts"]``
is pre-seeded to ``[]`` by the fetch gate; the ``make_evidence_callback``
after-callback then synthesises a no-data ``AnalystEvidence`` record for every
watchlist ticker so downstream consumers always receive a complete set.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_evidence_callback
from contract.extractors.smart_money import extract_smart_money_features

from .fetch import smart_money_fetch_callback
from .prompts import SMART_MONEY_INSTRUCTION

# Evidence-only after-callback: reads verdicts from state["smart_money_verdicts"],
# runs the smart-money feature extractor, and writes state["smart_money_evidence"].
# Missing verdicts (LLM skipped or gate short-circuited) produce no-data records
# via the callback's built-in fallback path — no special ``sparse`` flag needed.
_after = make_evidence_callback(
    analyst="smart_money",
    extractor=extract_smart_money_features,
    verdicts_state_key="smart_money_verdicts",
)


# Module-level singleton used by unit tests that construct the agent directly.
smart_money_analyst = LlmAgent(
    name="SmartMoneyAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=SMART_MONEY_INSTRUCTION,
    output_key="smart_money_verdicts",
    before_agent_callback=smart_money_fetch_callback,
    after_agent_callback=_after,
)


def _build_smart_money_analyst() -> LlmAgent:
    """Construct a fresh ``SmartMoneyAnalyst`` instance (orchestrator factory).

    Returns a brand-new ``LlmAgent`` wired with the same evidence-only
    callback, fetch gate, and prompt as the module-level singleton.
    """
    return LlmAgent(
        name="SmartMoneyAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=SMART_MONEY_INSTRUCTION,
        output_key="smart_money_verdicts",
        before_agent_callback=smart_money_fetch_callback,
        after_agent_callback=_after,
    )
