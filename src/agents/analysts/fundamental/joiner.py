# src/agents/analysts/fundamental/joiner.py
"""FundamentalJoinerAgent — consolidates per-ticker Fundamental verdicts into
the canonical contract keys (Phase 9).

Reads (per watchlist ticker):
  - temp:fundamental_verdict_<TICKER>  — TickerVerdict dict, or absent if the branch failed
  - temp:fundamental_data              — raw per-ticker fundamental dict (extractor input)
  - tickers, tick_id, as_of           — pipeline context

Yields one state_delta event carrying:
  - fundamental_verdicts  — VerdictBatch dict (the §A contract key)
  - fundamental_evidence  — list[AnalystEvidence] dumps

This is a symmetric mirror of NewsJoinerAgent (``news/joiner.py``) with every
``news`` identifier replaced by ``fundamental``.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from contract.evidence import AnalystEvidence, AnalystVerdict, TickerVerdict, VerdictBatch
from contract.extractors.fundamental import extract_fundamental_features
from data.timeguard import resolve_as_of
from observability.terminal_log import emit_analyst_summary
from observability.trace import _trace_maybe


class FundamentalJoinerAgent(BaseAgent):
    """Build fundamental_verdicts + fundamental_evidence from per-ticker working keys."""

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Read N temp:fundamental_verdict_<TICKER> keys; emit canonical contract keys.

        For each watchlist ticker:
          1. Look up ``temp:fundamental_verdict_<TICKER>``.  If absent (branch
             failed), synthesise a no-data ``AnalystVerdict``.
          2. Run ``extract_fundamental_features`` on the per-ticker raw data slice.
          3. Wrap (verdict, features) in an ``AnalystEvidence`` record.

        Build a ``VerdictBatch`` of all ``TickerVerdict`` rows and yield
        both canonical keys via one ``state_delta`` event.

        Args:
            ctx: ADK invocation context carrying the session state.

        Yields:
            One ``Event`` whose ``actions.state_delta`` carries:
              - ``fundamental_verdicts`` — VerdictBatch dict
              - ``fundamental_evidence`` — list of AnalystEvidence dicts
        """
        state    = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str       = state.get("tick_id", "unknown")
        data:    dict      = state.get("temp:fundamental_data", {}) or {}

        recorded_at: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="fundamental/joiner",
        )

        # Snapshot session state for extractors that read pipeline context.
        _to_dict = getattr(state, "to_dict", None)
        state_snapshot: dict = _to_dict() if callable(_to_dict) else dict(state)

        verdicts_list: list[TickerVerdict] = []
        evidence_list: list[dict]          = []

        for ticker in tickers:
            raw_v = state.get(f"temp:fundamental_verdict_{ticker}")

            if raw_v is None:
                # Branch failed (or LlmAgent omitted output) — synthesise a safe default.
                verdict = AnalystVerdict(
                    lean        = "neutral",
                    magnitude   = 0.0,
                    confidence  = 0.0,
                    rationale   = "no verdict from LLM",
                    key_factors = [],
                    is_no_data  = True,
                )
                ticker_verdict = TickerVerdict(ticker=ticker, **verdict.model_dump())
            else:
                # Validate against the strict schema.  ADK's output_schema
                # already validated once on write, but re-validate here so
                # downstream consumers can rely on the shape unconditionally.
                ticker_verdict = TickerVerdict.model_validate({**raw_v, "ticker": ticker})
                verdict = AnalystVerdict.model_validate(
                    {k: v for k, v in ticker_verdict.model_dump().items() if k != "ticker"}
                )

            verdicts_list.append(ticker_verdict)

            # Deterministic feature extractor — operates on the per-ticker slice.
            raw_slice = data.get(ticker, {}) or {}
            features  = extract_fundamental_features(
                raw_slice, ticker,
                as_of=recorded_at,
                state=state_snapshot,
            )

            ev = AnalystEvidence(
                analyst          = "fundamental",
                ticker           = ticker,
                tick_id          = tick_id,
                recorded_at      = recorded_at,
                verdict          = verdict,
                features         = features,
                feature_warnings = [],
            )
            evidence_list.append(ev.model_dump(mode="json"))

        batch = VerdictBatch(verdicts=verdicts_list)

        # ── Terminal summary row ──────────────────────────────────────────────
        # Read the accumulator written by the per-ticker after_model callbacks.
        # Each record was appended by ``make_observability_callbacks``'s after_cb
        # when a branch completed successfully.  Branches that crashed never
        # appended — they are counted as failures via the difference between
        # ticker_count and len(calls).
        #
        # Only emit when STOCKBOT_TERMINAL_LOG=1 (accumulator key present).
        _obs_calls: list[dict] = state.get("temp:_obs_fundamental_calls") or []
        if _obs_calls or tickers:
            # Always emit the summary when there are tickers — even if all failed
            # (empty accumulator) so the operator knows the analyst ran.
            import os
            if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
                emit_analyst_summary(
                    "fundamental",
                    calls=_obs_calls,
                    ticker_count=len(tickers),
                )

        # Surface trace — records the aggregated verdicts for debugging/auditing.
        _trace_maybe(state, "02_fundamental_verdict", [v.model_dump() for v in verdicts_list])

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "fundamental_verdicts": batch.model_dump(),
                "fundamental_evidence": evidence_list,
            }),
        )
