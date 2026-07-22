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

from config.analysts import get_analysts_config
from contract.evidence import (
    AnalystEvidence,
    AnalystVerdict,
    LlmTickerVerdict,
    TickerVerdict,
    VerdictBatch,
)
from contract.extractors.fundamental import (
    FILING_ANCHOR_ABSENT_SENTINEL,
    extract_fundamental_features,
)
from data.timeguard import resolve_as_of
from observability.terminal_log import emit_analyst_summary
from observability.trace import trace_maybe


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
                no_data_verdict = AnalystVerdict(
                    lean        = "neutral",
                    magnitude   = 0.0,
                    confidence  = 0.0,
                    rationale   = "no verdict from LLM",
                    key_factors = [],
                    is_no_data  = True,
                )
                ticker_verdict = TickerVerdict(ticker=ticker, **no_data_verdict.model_dump())
            else:
                # Validate against the strict LLM emit-schema first (re-validates
                # what ADK's output_schema already enforced on write, so downstream
                # consumers can rely on the shape unconditionally), then inflate
                # via the sole canonical-conversion method.  Raises loudly if the
                # post-conversion canonical shape is invalid.
                llm_v = LlmTickerVerdict.model_validate({**raw_v, "ticker": ticker})
                ticker_verdict = llm_v.to_ticker_verdict(
                    horizon_days=get_analysts_config().fundamental.filing_delta_horizon_days,
                )

            # Deterministic feature extractor — operates on the per-ticker slice.
            # Moved ahead of the clamp/decay block below (Task 8) so those steps
            # can read filing_anchor_days off the same features dict that lands
            # in AnalystEvidence — a single extraction, no duplicate work.
            raw_slice = data.get(ticker, {}) or {}
            features  = extract_fundamental_features(
                raw_slice, ticker,
                as_of=recorded_at,
                state=state_snapshot,
            )

            # ── Task 8: deterministic post-LLM magnitude clamp + decay ───────
            # The Fundamental prompt (Task 7) tells the LLM a downstream clamp
            # exists — this is it.  Lazy Prices ("The Lazy Prices" — Cohen,
            # Lou & Malloy) documents post-filing drift as a weak, gradual
            # per-name tilt, not a fresh material catalyst on the scale of an
            # earnings surprise or a thesis break.  An LLM asked to rate filing
            # deltas will occasionally over-score them (recency bias on
            # "interesting" prose); this bounds the blast radius deterministically
            # rather than trusting the LLM's self-restraint.
            fcfg = get_analysts_config().fundamental
            tags = ticker_verdict.key_factors or []

            # Going-concern is a genuine thesis-break catalyst (imminent
            # solvency risk) — categorically different from an ordinary filing
            # delta, so it re-anchors the thesis and bypasses the cap entirely.
            going_concern = any(
                t.startswith("going_concern") and t.endswith("true") for t in tags
            )

            if not going_concern and ticker_verdict.magnitude > fcfg.filing_delta_magnitude_cap:
                ticker_verdict = ticker_verdict.model_copy(
                    update={"magnitude": fcfg.filing_delta_magnitude_cap},
                )

            # Linear decay past horizon exhaustion, anchored to the most recent
            # periodic filing via the extractor's filing_anchor_days feature.
            # Applied AFTER the clamp — a decayed cap is still a cap, and this
            # keeps the two adjustments composable rather than order-dependent.
            # The decay guard runs regardless of going_concern: it is gated on
            # whether a periodic filing exists at all (sentinel check below),
            # not on the catalyst tag, so a stale going-concern verdict still
            # decays even though it was never clamped.
            if fcfg.filing_delta_decay:
                anchor_days = features.get("filing_anchor_days")
                horizon     = fcfg.filing_delta_horizon_days
                if (
                    anchor_days is not None
                    and anchor_days < FILING_ANCHOR_ABSENT_SENTINEL
                    and anchor_days > horizon
                ):
                    # Fully decayed once one full horizon has elapsed past exhaustion.
                    overshoot = min((anchor_days - horizon) / horizon, 1.0)
                    ticker_verdict = ticker_verdict.model_copy(
                        update={"magnitude": ticker_verdict.magnitude * (1.0 - overshoot)},
                    )

            verdicts_list.append(ticker_verdict)

            # Build the canonical AnalystVerdict from the FINAL (clamped and/or
            # decayed) ticker_verdict, so fundamental_verdicts and
            # fundamental_evidence can never diverge (F-analysts-016).
            verdict = AnalystVerdict.model_validate(
                {k: v for k, v in ticker_verdict.model_dump().items() if k != "ticker"}
            )

            ev = AnalystEvidence(
                analyst     = "fundamental",
                ticker      = ticker,
                tick_id     = tick_id,
                recorded_at = recorded_at,
                verdict     = verdict,
                features    = features,
            )
            evidence_list.append(ev.model_dump(mode="json"))

        batch = VerdictBatch(verdicts=verdicts_list)

        # ── Terminal summary row ──────────────────────────────────────────────
        # Collect per-ticker call records written by
        # ``make_observability_callbacks``'s after_cb (on LLM success) and
        # by ``cache_callbacks._before`` (on cache hit).  Each branch writes
        # to its own disjoint key ``temp:_obs_fundamental_call_<TICKER>``
        # so the parallel fan-out has no shared mutable state to race on
        # (see ``make_observability_callbacks`` docstring for the prior
        # shared-list bug this replaces).  Branches that crashed never
        # wrote — they are counted as failures via the difference between
        # ticker_count and len(_obs_calls).
        #
        # Only emit when STOCKBOT_TERMINAL_LOG=1.
        _obs_calls: list[dict] = []
        for t in tickers:
            rec = state.get(f"temp:_obs_fundamental_call_{t}")
            if rec is not None:
                _obs_calls.append(rec)

        _obs_retries: dict[str, int] = state.get("temp:_obs_fundamental_retries") or {}
        if _obs_calls or tickers:
            # Always emit the summary when there are tickers — even if all failed
            # (empty accumulator) so the operator knows the analyst ran.
            import os
            if os.environ.get("STOCKBOT_TERMINAL_LOG") == "1":
                emit_analyst_summary(
                    "fundamental",
                    calls        = _obs_calls,
                    ticker_count = len(tickers),
                    retries      = _obs_retries,
                )

        # Surface trace — records the aggregated verdicts for debugging/auditing.
        trace_maybe(state, "02_fundamental_verdict", [v.model_dump() for v in verdicts_list])

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={
                "fundamental_verdicts": batch.model_dump(),
                "fundamental_evidence": evidence_list,
            }),
        )
