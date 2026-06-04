"""RiskGate BaseAgent — deterministic constraints + order generation."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Final

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from broker.portfolio import Portfolio
from observability.trace import _trace_maybe
from orchestrator.state import MIN_HELD_WEIGHT

from .constraints import apply_constraints
from .orders import weights_to_orders

# Stances whose intent is non-trading (update = thesis refresh, no_action =
# explicit hold). Risk caps are irrelevant for these — they must bypass the
# weight-clamp path entirely. Canonical four-verb vocabulary: buy / sell /
# update / no_action (see src/agents/strategist/schema.py). No compatibility
# shim for the pre-iter-3 "hold" verb — strategist will never emit it.
_NO_RISK_GATE_INTENTS: Final[frozenset[str]] = frozenset({"update", "no_action"})


class RiskGateInputError(RuntimeError):
    """Raised when RiskGate is invoked with missing or malformed inputs.

    These are wiring bugs — the strategist contract guarantees a decision
    object on every tick (even one with stances=[]). Falling through silently
    would hide pipeline breakage as 'no orders this tick'.
    """


class RiskGateAgent(BaseAgent):
    """Pure-Python deterministic agent that sits between the Strategist and the Executor.

    Responsibilities:
    1. Clamp buy-stance deltas to ``max_delta_per_buy`` (defence-in-depth
       — the TickerStance schema already enforces this at construction time
       using the same config field).
    2. Clamp target weights to satisfy hard risk rules (concentration, cash
       floor, total turnover).
    3. Validate position lifecycle contracts (sell stance required for any closing).
    4. Convert the clamped weights into concrete broker Orders.

    No LLM calls — this agent is fast and fully deterministic.
    """

    name: str = "RiskGate"
    broker: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        from agents.strategist.schema import StrategistDecision

        state = ctx.session.state
        decision_raw = state.get("strategist_decision")
        if not decision_raw:
            # A-001 — silent return masked upstream wiring breakage. The strategist
            # contract guarantees a decision (even one with stances=[]) on every
            # tick; absence here means the pipeline is broken, not "no orders".
            raise RiskGateInputError(
                "risk_gate invoked without strategist_decision — strategist "
                "must produce a (possibly empty) StrategistDecision every tick"
            )

        decision = (
            StrategistDecision.model_validate(decision_raw)
            if isinstance(decision_raw, dict)
            else decision_raw
        )

        # ── Load risk-gate config (passed into apply_constraints below) ─────────
        # Used to drive both the buy-delta clamp and the weight-level rules.
        from config.risk_gate import get_risk_gate_config as _get_rg_cfg
        _rg_config = _get_rg_cfg()

        proposed = dict(decision.target_weights)

        # Strip update (and legacy hold) stances from ``proposed`` before
        # clamping.  These stances carry no weight change — the executor will
        # skip broker dispatch for them (``resolve_broker_call`` returns
        # ``None``).  Leaving their tickers in the proposed dict would run the
        # clamp logic against a stale/zero weight, which is semantically wrong.
        _stance_intents = {
            s.ticker: s.intent
            for s in (decision.stances or [])
            if s.intent is not None
        }
        for _ticker, _intent in list(_stance_intents.items()):
            if _intent in _NO_RISK_GATE_INTENTS:
                # Preserve the current weight in ``proposed`` unchanged —
                # do not clip or zero it out.
                # If the ticker is missing from target_weights (expected),
                # we simply leave it absent from ``proposed`` too.
                proposed.pop(_ticker, None)

        # A-034 — full closes (sell with weight=None) bypass clamps because
        # capping them at a max-turnover or cash-floor scale would leave
        # dust shares behind (D3 contract trap).  Excluding them from
        # ``proposed`` before ``apply_constraints`` means the clamp
        # telemetry reflects what actually constrained output — the old
        # post-clamp restoration write that used to live here distorted
        # telemetry and is now removed.
        _close_tickers = {
            s.ticker
            for s in (decision.stances or [])
            if s.intent == "sell" and s.weight is None   # absent weight = full close
        }

        # Snapshot the full proposed dict (including full-close tickers at 0.0)
        # BEFORE exclusion — the lifecycle check below iterates ``original_weights``
        # to detect closings and needs to see AAPL (weight → 0.0) even though it
        # will be excluded from the clamping domain.
        original_weights = dict(proposed)

        # Surface trace — record the weights entering the clamp loop.
        # NOTE: ``proposed`` at this point still includes full-close tickers at
        # 0.0 (e.g. AAPL when the strategist emits a weight=None sell stance).
        # Those tickers are excluded from ``proposed_for_clamp`` (built below)
        # and therefore never enter the clamp domain — they will not generate
        # any ClampRecord.  The trace key is intentionally kept as
        # ``"06_risk_gate_in"`` so dashboards are not broken by a rename.
        _trace_maybe(state, "06_risk_gate_in", {"proposed_weights": proposed})

        # A-072: consume state['portfolio'] (refreshed at Phase 2) rather
        # than re-pulling from the broker mid-tick.  The broker remains
        # the source of truth, but the Phase 2 refresh already canonicalised
        # it into state for every downstream agent.
        portfolio_value = state.get("portfolio")
        if portfolio_value is None:
            # Cold-start carve-out: no portfolio seeded yet → no historical
            # weights to clamp against.  Raise rather than silently allow
            # unbounded weights — cold-start callers should seed
            # state['portfolio'] explicitly.
            raise RuntimeError(
                "risk_gate: state['portfolio'] missing — Phase 2 seed "
                "did not run."
            )

        portfolio = Portfolio.from_state_value(portfolio_value)
        current_weights = portfolio.current_weights()

        # ── Step 2: build price map (A-002, A-005) ──────────────────────────────
        # Prices for already-held positions come from portfolio.positions
        # (each Position carries the last_price refreshed in Phase 2).
        #
        # Prices for unheld BUY tickers come from state["reference_prices"],
        # which is seeded by:
        #   • live:    orchestrator/tick.py _build_initial_state / _fetch_reference_prices
        #   • backtest: backtest/driver.py _seed_reference_prices
        #
        # state["reference_prices"] shape (one entry per symbol):
        #   {
        #     "NVDA": {
        #       "ticker": "NVDA",
        #       "bars": [
        #         {"timestamp": "...", "open": 940.0, "high": 955.0,
        #          "low": 938.0, "close": 950.0, "volume": 1000000},
        #         ...
        #       ]
        #     }
        #   }
        # PIT clamping trims bars to ≤ as_of on every tick, so bars[-1]
        # is always the correct point-in-time last bar; close is the price.
        #
        # The old hasattr(self.broker, "_prices") block was removed (A-002/A-005):
        # it reached into a FakeBroker-only private attribute that has no
        # Trading 212 equivalent, producing silently wrong prices in production
        # for any ticker not already in portfolio.positions.
        prices = {t: pos.last_price for t, pos in portfolio.positions.items()}

        reference_prices: dict = state.get("reference_prices") or {}
        for sym, payload in reference_prices.items():
            # Skip tickers the portfolio already prices via last_price.
            if sym in prices:
                continue

            # Guard: payload must be a dict with a non-empty "bars" list.
            bars = payload.get("bars") if isinstance(payload, dict) else None
            if not bars:
                continue

            # Guard: bars[-1] must be a dict before calling .get — a malformed
            # payload (e.g. a bare number or None) would otherwise raise an
            # opaque AttributeError rather than a clear failure.
            #
            # Note: skipping here is NOT silent degradation.  Any ticker that
            # falls out of ``prices`` will trigger
            # ``ValueError("no price for <ticker>")`` inside
            # ``weights_to_orders``, so the malformed payload surfaces loudly
            # on the very same tick rather than being swallowed.
            last_bar = bars[-1]
            if not isinstance(last_bar, dict):
                continue

            # bars[-1]["close"] is the PIT-clamped last close.
            close = last_bar.get("close")
            if close is None:
                continue

            prices[sym] = float(close)

        # Remove full-close tickers from the clamping domain — they will be
        # re-added at 0.0 after clamping.  This prevents a max-turnover or
        # cash-floor rescale from generating a false ClampRecord for a ticker
        # whose emitted weight is unconditionally 0.0 (the D3 contract
        # guarantees a full close reaches exactly 0.0, not a scaled delta).
        proposed_for_clamp = {t: w for t, w in proposed.items() if t not in _close_tickers}

        # Apply all constraints in order: buy-delta (stance-level) first, then
        # the four weight-level rules.  Full-close tickers are absent from
        # proposed_for_clamp so no clamp record can be produced for them.
        # stances/config are required keyword args — omitting either would
        # silently skip the buy-delta clamp.
        clamps = apply_constraints(
            proposed_for_clamp,
            current_weights,
            stances=decision.stances or [],
            config=_rg_config,
        )

        # Reassemble final proposed: clamped non-close weights + full-close
        # targets pinned at exactly 0.0 (no restoration needed — they were
        # never in the clamping domain to begin with).
        proposed = dict(proposed_for_clamp)
        for _t in _close_tickers:
            proposed[_t] = 0.0

        # Lifecycle check — only closing positions need an explicit sell stance.
        # The rationale lives on the stance now (A-013 tail collapse — the
        # sell_reasons dict was deleted); derive the closing set from stances.
        selling = {s.ticker for s in (decision.stances or []) if s.intent == "sell"}
        for t, new_w in original_weights.items():
            was_open     = current_weights.get(t, 0.0) >= MIN_HELD_WEIGHT
            will_be_open = new_w >= MIN_HELD_WEIGHT
            if was_open and not will_be_open and t not in selling:
                from agents.strategist.derivation import StrategistContractViolation
                raise StrategistContractViolation(
                    f"Closing {t} ({current_weights.get(t)} -> {new_w}) without sell stance"
                )

        orders = weights_to_orders(proposed, portfolio, prices) if self.broker else []

        # Snapshot the JSON-friendly payloads into local variables so the
        # trace (below) and the yielded ``state_delta`` (further below)
        # both reference the same in-memory list rather than reading
        # back through ``state`` (which, post-Rule-1, the agent no longer
        # writes to directly).
        final_orders        = [o.model_dump() for o in orders]
        risk_clamps_applied = [c.model_dump() for c in clamps]

        # Surface trace — record clamped weights and generated orders.
        # Reads from the local variables, not from ``state``, because the
        # state_delta has not been merged yet at this point.
        _trace_maybe(state, "06_risk_gate_out", {
            "clamped_weights": proposed,
            "orders":          final_orders,
            "clamps":          risk_clamps_applied,
        })

        # Contract Rule 1 — yield a single Event whose state_delta
        # carries both writes.  RiskGate's output handshake to the
        # Executor (final_orders) and to observability
        # (risk_clamps_applied) is one logical step; co-emitting keeps
        # the merge atomic on the SessionService.  See
        # ``docs/contract-invariants.md`` §C-Rule 1.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "final_orders":        final_orders,
                "risk_clamps_applied": risk_clamps_applied,
            }),
        )
