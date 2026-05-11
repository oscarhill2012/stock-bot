"""Smart-money analyst LlmAgent with dual-emit (legacy signal + new evidence).

Smart-money is the only analyst flagged as ``sparse`` in the dual-emit helper:
its ``before_agent_callback`` can short-circuit the LLM entirely when no
material activity is detected across the watchlist. The exhaustive-validator
behaviour used by the other three analysts would re-prompt a skipped LLM, so
the sparse flag swaps that out for an empty-evidence write on the skip path
(and a tolerant non-exhaustive write when the LLM does run).
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.smart_money import extract_smart_money_features

from .fetch import smart_money_fetch_callback
from .prompts import SMART_MONEY_INSTRUCTION
from .schema import SmartMoneySignal

# Dual-emit callback: writes legacy state["smart_money_signals"] alongside the
# new state["smart_money_evidence"]. ``sparse=True`` disables the exhaustive
# re-prompt because the smart-money gate is allowed to short-circuit with an
# empty signal list when nothing material was filed.
_after = make_dual_emit_callback(
    analyst="smart_money",
    signals_key="smart_money_signals",
    data_key="smart_money_data",
    evidence_key="smart_money_evidence",
    extractor=extract_smart_money_features,
    sparse=True,
)


# Module-level singleton used by unit tests that construct the agent directly.
smart_money_analyst = LlmAgent(
    name="SmartMoneyAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=SMART_MONEY_INSTRUCTION,
    output_schema=list[SmartMoneySignal],
    output_key="smart_money_signals",
    before_agent_callback=smart_money_fetch_callback,
    after_agent_callback=_after,
)


def _build_smart_money_analyst() -> LlmAgent:
    """Construct a fresh ``SmartMoneyAnalyst`` instance (orchestrator factory).

    Returns a brand-new ``LlmAgent`` wired with the same dual-emit callback,
    fetch gate, prompt, and output schema as the module-level singleton.
    """
    return LlmAgent(
        name="SmartMoneyAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=SMART_MONEY_INSTRUCTION,
        output_schema=list[SmartMoneySignal],
        output_key="smart_money_signals",
        before_agent_callback=smart_money_fetch_callback,
        after_agent_callback=_after,
    )
