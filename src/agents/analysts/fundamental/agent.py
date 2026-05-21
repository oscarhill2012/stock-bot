"""Fundamental analyst SequentialAgent branch — per-ticker fan-out (Phase 9).

Builds: SequentialAgent[FundamentalFetchAgent, *per-ticker branches,
FundamentalJoinerAgent]

Where each per-ticker branch is
``IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))`` constructed via
``build_fundamental_branch_for_ticker``.  Composition is done here rather
than in ``orchestrator.pipeline`` so the per-tick pipeline build (driver.py /
tick.py) calls a single factory.

The legacy ``build_fundamental_analyst`` factory (one LlmAgent over a
``VerdictBatch``) is retired in Phase 9 — every call site (pipeline, tests)
is updated to use ``build_fundamental_branch`` instead.  See
``docs/Phase9-agent-fanning-per-ticker/spec.md``.

The ``_fundamental_hash_inputs_from_dict`` helper that previously lived here
has been moved to ``agents.analysts.report_cache`` (as
``fundamental_hash_inputs_from_dict``) so both this module and
``fundamental/per_ticker.py`` can import it without creating a circular
dependency between agent.py and per_ticker.py.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.analysts.fundamental.fetch_agent import FundamentalFetchAgent
from agents.analysts.fundamental.joiner import FundamentalJoinerAgent
from agents.analysts.fundamental.per_ticker import build_fundamental_branch_for_ticker
from agents.analysts.heuristics import FundamentalVocabulary


def build_fundamental_branch(
    vocab: FundamentalVocabulary,
    *,
    tickers: list[str],
) -> SequentialAgent:
    """Construct the per-tick Fundamental analyst branch from the current watchlist.

    Args:
        vocab:    Validated FundamentalVocabulary holding the closed-vocab
                  tag lists (guidance, tone, risks, insider_signals).
        tickers:  The watchlist as known at pipeline-build time.  An empty
                  list is permitted — the branch becomes a fetch+joiner
                  no-op that still emits canonical (empty)
                  fundamental_verdicts / fundamental_evidence so downstream
                  consumers see consistent shapes.

    Returns:
        SequentialAgent named ``"FundamentalAnalystBranch"`` composed of
        ``[FundamentalFetchAgent, *per-ticker branches,
        FundamentalJoinerAgent]``.

    The caller (``orchestrator.pipeline._build_analyst_pool``) is
    responsible for invoking this once per tick with the current watchlist
    — see the Phase 9 spec §7 for the per-tick rebuild rationale.
    """
    # Build one isolated per-ticker LlmAgent branch for every ticker in
    # the watchlist.  An empty watchlist is valid — the SequentialAgent
    # then contains only [FetchAgent, JoinerAgent] which is a no-op pass.
    per_ticker = [
        build_fundamental_branch_for_ticker(ticker, vocab) for ticker in tickers
    ]

    return SequentialAgent(
        name="FundamentalAnalystBranch",
        sub_agents=[
            FundamentalFetchAgent(name="FundamentalFetch"),
            *per_ticker,
            FundamentalJoinerAgent(name="FundamentalJoiner"),
        ],
    )
