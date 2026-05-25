# src/agents/analysts/news/joiner.py
"""NewsJoinerAgent — consolidates per-ticker News verdicts into the canonical
contract keys (Phase 9).

Reads (per watchlist ticker):
  - temp:news_verdict_<TICKER>  — TickerVerdict dict, or absent if the branch failed
  - temp:news_data              — raw per-ticker news dict (extractor input)
  - tickers, tick_id, as_of     — pipeline context

Yields one state_delta event carrying:
  - news_verdicts  — VerdictBatch dict (the §A contract key)
  - news_evidence  — list[AnalystEvidence] dumps
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from contract.evidence import AnalystEvidence, AnalystVerdict, TickerVerdict, VerdictBatch
from contract.extractors.news import extract_news_features
from data.timeguard import resolve_as_of
from observability.terminal_log import emit_analyst_summary
from observability.trace import _trace_maybe


class NewsJoinerAgent(BaseAgent):
    """Build news_verdicts + news_evidence from per-ticker working keys."""

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Read N temp:news_verdict_<TICKER> keys; emit canonical contract keys.

        For each watchlist ticker:
          1. Look up ``temp:news_verdict_<TICKER>``.  If absent (branch
             failed), synthesise a no-data ``AnalystVerdict``.
          2. Run ``extract_news_features`` on the per-ticker raw data slice.
          3. Wrap (verdict, features) in an ``AnalystEvidence`` record.

        Build a ``VerdictBatch`` of all ``TickerVerdict`` rows and yield
        both canonical keys via one ``state_delta`` event.
        """
        state    = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str       = state.get("tick_id", "unknown")
        data:    dict      = state.get("temp:news_data", {}) or {}

        recorded_at: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="news/joiner",
        )

        # Snapshot session state for extractors that read pipeline context.
        _to_dict = getattr(state, "to_dict", None)
        state_snapshot: dict = _to_dict() if callable(_to_dict) else dict(state)

        verdicts_list: list[TickerVerdict] = []
        evidence_list: list[dict]          = []

        for ticker in tickers:
            raw_v = state.get(f"temp:news_verdict_{ticker}")

            if raw_v is None:
                # Branch failed (or LlmAgent omitted output) — synthesise.
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
            features  = extract_news_features(
                raw_slice, ticker,
                as_of=recorded_at,
                state=state_snapshot,
            )

            ev = AnalystEvidence(
                analyst        = "news",
                ticker         = ticker,
                tick_id        = tick_id,
                recorded_at    = recorded_at,
                verdict        = verdict,
                features       = features,
                feature_warnings = [],
            )
            evidence_list.append(ev.model_dump(mode="json"))

        batch = VerdictBatch(verdicts=verdicts_list)

        # ── Terminal summary row ──────────────────────────────────────────────
        # Collect per-ticker call records written by
        # ``make_observability_callbacks``'s after_cb (on LLM success) and
        # by ``cache_callbacks._before`` (on cache hit).  Each branch writes
        # to its own disjoint key ``temp:_obs_news_call_<TICKER>`` so the
        # parallel fan-out has no shared mutable state to race on (see
        # ``make_observability_callbacks`` docstring for the prior
        # shared-list bug this replaces).  Branches that crashed never
        # wrote — they are counted as failures via the difference between
        # ticker_count and len(_obs_calls).
        #
        # Only emit when STOCKBOT_TERMINAL_LOG=1.
        _obs_calls: list[dict] = []
        for t in tickers:
            rec = state.get(f"temp:_obs_news_call_{t}")
            if rec is not None:
                _obs_calls.append(rec)

        _obs_retries: dict[str, int]  = state.get("temp:_obs_news_retries") or {}
        if _obs_calls or tickers:
            # Always emit the summary when there are tickers — even if all failed
            # (empty accumulator) so the operator knows the analyst ran.
            import os
            if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
                emit_analyst_summary(
                    "news",
                    calls        = _obs_calls,
                    ticker_count = len(tickers),
                    retries      = _obs_retries,
                )

        # Surface trace — records the aggregated verdicts for debugging/auditing.
        _trace_maybe(state, "02_news_verdict", [v.model_dump() for v in verdicts_list])

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "news_verdicts": batch.model_dump(),
                "news_evidence": evidence_list,
            }),
        )
