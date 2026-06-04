"""Unit tests for the risk-gate buy-delta clamp (A-058 — folded into apply_constraints),
and for the full-close clamp-telemetry fix (A-034).

A-058: ``apply_constraints`` now owns the full clamp sequence.  The
buy-delta step runs first (stance-level, defence-in-depth) and is
activated via the required ``stances`` and ``config`` keyword arguments.
This is defence-in-depth: the ``TickerStance`` schema already forbids
``weight > max_delta_per_buy`` at construction time; the risk-gate clamp
fires if a caller ever bypasses that validation (e.g. by constructing the
object via ``model_construct`` without validators).

A-034 fix — full-close bypass
------------------------------
Full-close stances (sell with weight=None) must be excluded from the
clamping domain entirely.  The old code included them in ``proposed`` before
calling ``apply_constraints``, then restored 0.0 afterwards — meaning a
max_turnover rescale could produce a false ClampRecord for the ticker even
though the emitted weight was forced back to 0.0.  The fix excludes them
up-front so no clamp record is generated.

Interface under test
--------------------
``constraints.apply_constraints(proposed, current, *, stances, config)``
    The buy-delta step mutates ``stances`` in-place (clamping weight on
    any buy stance that exceeds ``config.max_delta_per_buy``) and emits
    ``buy_delta_exceeded`` ClampRecords first in the returned list.  The
    weight-level steps follow (no-short, max_position, cash_floor,
    max_turnover).

We also test the ``position_cap_exceeded`` path (the existing
``max_position`` clamp from ``apply_constraints``) to confirm it still
fires correctly under the new config-driven code path.
"""
from __future__ import annotations

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────

def _make_buy_stance(ticker: str, weight: float):
    """Construct a TickerStance via model_construct to bypass the schema
    cap and let the risk-gate clamp fire (the scenario being tested).

    ``model_construct`` skips Pydantic validators — we deliberately
    want a weight above the schema cap so the risk-gate clamp has
    something to act on.

    Parameters
    ----------
    ticker:  The stock ticker symbol.
    weight:  Target buy-delta weight (may exceed the schema-level cap).

    Returns
    -------
    TickerStance with intent='buy'.
    """
    from agents.strategist.stance_schema import TickerStance
    return TickerStance.model_construct(
        ticker=ticker,
        intent="buy",
        weight=weight,
        rationale="test bypass — validators skipped intentionally",
        catalyst=None,
    )


# ── buy-delta clamp tests ─────────────────────────────────────────────────────

def test_buy_delta_at_cap_passes_unchanged():
    """A buy stance whose weight equals max_delta_per_buy should pass through
    the clamp without modification and produce no clamp record."""
    from agents.risk_gate.constraints import apply_constraints
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    stance = _make_buy_stance("AAPL", cfg.max_delta_per_buy)
    proposed = {"AAPL": cfg.max_delta_per_buy}
    current: dict[str, float] = {}

    clamps = apply_constraints(proposed, current, stances=[stance], config=cfg)

    assert stance.weight == cfg.max_delta_per_buy
    # Only weight-level clamps may appear; no buy_delta_exceeded record.
    assert not any(c.rule == "buy_delta_exceeded" for c in clamps)


def test_buy_delta_below_cap_passes_unchanged():
    """A buy stance whose weight is well below the cap should be untouched."""
    from agents.risk_gate.constraints import apply_constraints
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    stance = _make_buy_stance("TSLA", 0.02)  # below cap
    proposed = {"TSLA": 0.02}
    current: dict[str, float] = {}

    clamps = apply_constraints(proposed, current, stances=[stance], config=cfg)

    assert stance.weight == pytest.approx(0.02)
    # No buy_delta_exceeded record for a below-cap stance.
    assert not any(c.rule == "buy_delta_exceeded" for c in clamps)


def test_buy_delta_above_cap_is_clamped():
    """A buy stance whose weight exceeds max_delta_per_buy must be
    clamped to the cap and a ClampRecord with reason 'buy_delta_exceeded'
    must be emitted.

    This is the core defence-in-depth scenario: a caller that bypassed the
    schema validator (e.g. via model_construct) still gets clamped at the
    risk-gate layer.
    """
    from agents.risk_gate.constraints import apply_constraints
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    over_cap = cfg.max_delta_per_buy + 0.03   # any delta above the configured cap

    stance = _make_buy_stance("NVDA", over_cap)
    proposed = {"NVDA": over_cap}
    current: dict[str, float] = {}

    clamps = apply_constraints(proposed, current, stances=[stance], config=cfg)

    # Stance weight must be clamped to the cap (proposed dict is decoupled — untouched
    # by the buy-delta step).
    assert stance.weight == pytest.approx(cfg.max_delta_per_buy)

    # At least one buy_delta_exceeded record must be emitted, and it must be first.
    buy_delta_clamps = [c for c in clamps if c.rule == "buy_delta_exceeded"]
    assert len(buy_delta_clamps) == 1
    assert buy_delta_clamps[0].rule == "buy_delta_exceeded"
    assert buy_delta_clamps[0].ticker == "NVDA"
    assert buy_delta_clamps[0].before == pytest.approx(over_cap)
    assert buy_delta_clamps[0].after == pytest.approx(cfg.max_delta_per_buy)
    # buy_delta_exceeded record must come before any weight-level records.
    assert clamps.index(buy_delta_clamps[0]) == 0


def test_sell_and_update_stances_are_not_clamped():
    """Sell and update stances must pass through the buy-delta clamp
    untouched — the clamp is buy-only."""
    from agents.risk_gate.constraints import apply_constraints
    from agents.strategist.stance_schema import TickerStance
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()

    # Sell with explicit partial weight (within sell range).
    sell_stance = TickerStance(ticker="MSFT", intent="sell", weight=0.10, rationale="exit")
    update_stance = TickerStance(ticker="GOOG", intent="update", rationale="thesis revision")

    proposed = {"MSFT": 0.10}
    current: dict[str, float] = {}

    clamps = apply_constraints(
        proposed,
        current,
        stances=[sell_stance, update_stance],
        config=cfg,
    )

    assert sell_stance.weight == pytest.approx(0.10)
    # No buy_delta_exceeded record — neither stance is a buy.
    assert not any(c.rule == "buy_delta_exceeded" for c in clamps)


def test_multiple_buys_all_clamped():
    """All buy stances above the cap in a mixed list are clamped; below-cap
    stances are left unchanged. The returned list contains exactly one
    buy_delta_exceeded record per clamped stance."""
    from agents.risk_gate.constraints import apply_constraints
    from config.risk_gate import load_risk_gate_config

    cfg = load_risk_gate_config()
    cap = cfg.max_delta_per_buy

    over1 = _make_buy_stance("AAPL", cap + 0.02)   # will be clamped
    over2 = _make_buy_stance("TSLA", cap + 0.05)   # will be clamped
    under = _make_buy_stance("AMZN", cap - 0.01)   # will NOT be clamped

    proposed = {"AAPL": cap + 0.02, "TSLA": cap + 0.05, "AMZN": cap - 0.01}
    current: dict[str, float] = {}

    clamps = apply_constraints(
        proposed,
        current,
        stances=[over1, over2, under],
        config=cfg,
    )

    # Stances for AAPL and TSLA must be clamped to cap; AMZN untouched.
    assert over1.weight == pytest.approx(cap)
    assert over2.weight == pytest.approx(cap)
    assert under.weight == pytest.approx(cap - 0.01)

    # Exactly two buy_delta_exceeded records, one for each over-cap stance.
    buy_delta_clamps = [c for c in clamps if c.rule == "buy_delta_exceeded"]
    assert len(buy_delta_clamps) == 2
    assert {c.ticker for c in buy_delta_clamps} == {"AAPL", "TSLA"}
    assert all(c.rule == "buy_delta_exceeded" for c in buy_delta_clamps)


# ── position-cap clamp integration test ─────────────────────────────────────

def test_position_cap_clamp_fires_via_apply_constraints():
    """A proposed weight above MAX_POSITION_WEIGHT triggers a
    'max_position' ClampRecord in apply_constraints.

    This confirms the existing position-cap logic still works correctly
    alongside the new buy-delta clamp.
    """
    from agents.risk_gate.constraints import apply_constraints
    from config.risk_gate import load_risk_gate_config

    proposed = {"AAPL": 0.99}   # far above 0.20 cap
    current  = {"AAPL": 0.18}

    cfg = load_risk_gate_config()
    clamps = apply_constraints(proposed, current, stances=[], config=cfg)

    position_clamps = [c for c in clamps if c.rule == "max_position"]
    assert position_clamps, "expected a max_position ClampRecord"
    assert position_clamps[0].ticker == "AAPL"
    assert position_clamps[0].after == pytest.approx(0.20)


# ── A-034: full-close clamp-telemetry fix ────────────────────────────────────


@pytest.mark.asyncio
async def test_full_close_does_not_appear_in_clamp_telemetry(
    fake_broker_factory,
    _invocation_context_with_state,
):
    """A-034 — full-close (sell with weight=None) must bypass clamp logic
    entirely.  ``risk_clamps_applied`` must NOT contain a record for the
    closed ticker.

    Scenario
    --------
    AAPL is held at weight ~0.20 in the portfolio.  The strategist emits a
    full-close sell stance for AAPL (weight=None) alongside two max-sized buy
    stances (NVDA 0.20, MSFT 0.20).  Total turnover = 0.20 (AAPL close
    delta) + 0.20 (NVDA) + 0.20 (MSFT) = 0.60, which exceeds
    ``max_total_turnover = 0.50``.

    OLD behaviour: ``apply_constraints`` ran with AAPL at 0.0 in the
    proposed dict; the max-turnover rescale changed AAPL's weight to a
    positive dust value and emitted a ``max_turnover`` ClampRecord for it;
    the restoration loop wrote it back to 0.0 but the false ClampRecord
    persisted in telemetry.

    FIXED behaviour: AAPL is excluded from ``proposed`` before
    ``apply_constraints`` runs, so no clamp record for AAPL is ever
    generated.  The emitted order is still a full SELL.

    Portfolio construction
    ----------------------
    Portfolio total = £100 000.
    AAPL position: 20 shares × £1 000 = £20 000 market value.
    Cash: £80 000.
    Total: £100 000 → AAPL weight = 0.20.
    """
    from agents.risk_gate.agent import RiskGateAgent
    from agents.strategist.stance_schema import TickerStance
    from broker.portfolio import Portfolio, Position

    # ── Build a portfolio where AAPL is held at exactly 0.20 weight ──────────
    # Total portfolio = cash (£80 000) + AAPL (20 shares × £1 000 = £20 000)
    # → AAPL weight = 20 000 / 100 000 = 0.20.
    aapl_position = Position(quantity=20, avg_cost=1000.0, last_price=1000.0)
    portfolio = Portfolio(
        cash=80_000.0,
        positions={"AAPL": aapl_position},
    )

    # ── Stances: full close AAPL + two max-delta buys (NVDA, MSFT) ──────────
    # The two buys push total turnover to 0.60 (> max_total_turnover 0.50),
    # triggering the max_turnover clamp.  Under the old code that clamp would
    # also produce a false ClampRecord for AAPL.
    # Build the stances directly.  sell_reasons / update_reasons were removed
    # (A-013 tail); the sell reason lives on the stance itself.
    from agents.strategist.schema import StrategistDecision

    stances = [
        TickerStance(ticker="AAPL", intent="sell", weight=None, rationale="thesis broken — exiting fully"),
        TickerStance(ticker="NVDA", intent="buy",  weight=0.20, rationale="strong momentum"),
        TickerStance(ticker="MSFT", intent="buy",  weight=0.20, rationale="earnings catalyst"),
    ]
    decision = StrategistDecision(
        stances        = stances,
        # Full close lands at 0.0; buys contribute their delta weights.
        target_weights = {"AAPL": 0.0, "NVDA": 0.20, "MSFT": 0.20},
        decision_tag   = "test_a034",
        reasoning      = "A-034 telemetry regression test",
        confidence     = 0.5,
    )

    # ── Seed reference_prices so NVDA and MSFT can be priced for order sizing
    reference_prices = {
        "NVDA": {"bars": [{"close": 500.0}]},
        "MSFT": {"bars": [{"close": 300.0}]},
    }

    ctx = _invocation_context_with_state(state={
        "strategist_decision": decision.model_dump(mode="json"),
        "portfolio":           portfolio.model_dump(mode="json"),
        "reference_prices":    reference_prices,
    })

    # Broker is required for orders to be generated (passed as non-None).
    broker = fake_broker_factory(
        positions={"AAPL": {"quantity": 20, "avg_cost": 1000.0, "last_price": 1000.0}},
        prices={"AAPL": 1000.0, "NVDA": 500.0, "MSFT": 300.0},
    )
    agent = RiskGateAgent(broker=broker)

    # ── Run the agent and collect the single emitted event ───────────────────
    events = [e async for e in agent._run_async_impl(ctx)]
    assert len(events) == 1, "risk gate must emit exactly one event"

    delta = events[0].actions.state_delta

    # ── Assert: AAPL must NOT appear in clamp telemetry ──────────────────────
    # ClampRecords are serialised to dicts via model_dump() in the agent —
    # the state_delta contains plain dicts, not ClampRecord objects.
    risk_clamps_applied = delta.get("risk_clamps_applied", [])
    aapl_clamps = [c for c in risk_clamps_applied if c.get("ticker") == "AAPL"]
    assert aapl_clamps == [], (
        "Full-close must not produce clamp telemetry — clamping a full-close "
        "ticker distorts the audit trail (the restoration to 0.0 meant the "
        "clamp did not constrain the emitted weight)."
    )

    # ── Assert: a SELL order must still be emitted for AAPL ──────────────────
    final_orders = delta.get("final_orders", [])
    aapl_orders = [o for o in final_orders if o.get("ticker") == "AAPL"]
    assert len(aapl_orders) == 1, "expected exactly one AAPL order"
    assert aapl_orders[0]["action"] == "SELL", (
        "full-close must still generate a SELL order even though it bypasses "
        "the clamp domain"
    )
