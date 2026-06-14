# Strategist Stance Schema Rewrite — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the strategist's six-verb stance vocabulary into three (`buy` / `sell` / `update`), drop the hallucination-prone numerical fields (`target_price` / `stop_price` / `horizon`), and move to a selective-output model where silence means "no change to last stated view."

**Architecture:** The strategist emits a sparse list of `TickerStance` objects per tick. Each stance is one of three verbs: `buy` (delta-sized, capped at 5 %), `sell` (delta-sized or full close, uncapped beyond current holding), `update` (prose-only thesis revision). Held positions with no stance emitted carry forward their last view. On the first tick of a window the strategist must emit a stance for every watchlist ticker (baseline establishment); on subsequent ticks only when something has changed. The downstream executor maps `buy → BUY` and `sell → SELL`; `update` produces no order but persists the new thesis prose.

**Tech Stack:** Python 3, Pydantic v2 (schema), Google ADK (agents), SQLAlchemy (persistence), pytest (tests).

**Background:** This plan is the iter-3 follow-on from the audit at `docs/backtest-audits/baseline-window-2025-09-iter-2.md`. The audit identified 10 bugs; Bug #9 (hallucinated target/stop prices — 80 % of opens) and Bug #10 (the iter-1 `add` fix did not change behaviour) both point to over-structuring as the load-bearing cause. This plan addresses those directly. Bug #8 (premature exits) is a separate prompt-discipline change that ships alongside in the same iteration but in tasks 9-10.

**Decisions made during planning** (recorded so future me can audit them):

- **Three verbs, not four** — `hold` is implicit by omission; no explicit `hold` verb. Silence = "no change to my last stated view."
- **`update` stays as a verb** — gives the strategist a way to revise prose thesis without trading.
- **Sell has no per-trade cap; buy has a 5 % delta cap per trade** — asymmetric sizing matches asymmetric risk (concentration vs deconcentration).
- **No deterministic drawdown floor in iter-3** — user decision: trust the cleaner schema first, add the floor only if iter-3 backtest shows catastrophic exposure.
- **20 % max-per-position cap unchanged** — already in `config/risk_gate.json`.
- **Thesis staleness visible, not enforced** — `temp:held_positions_view` shows "thesis last updated N ticks ago" but doesn't force a refresh.
- **`PositionThesis` loses `target_price` / `stop_price` / `horizon` fields** — keeps `rationale`, `catalyst`, `opened_price`, `opened_at`. Pre-deployment per memory, so no DB migration concerns beyond test fixtures.
- **Decision JSON files from iter-1 / iter-2 are immutable artefacts** — not replayed in tests; no backwards-compat needed.

---

## File Structure

Files modified by this plan and their responsibilities post-rewrite:

| File | Responsibility | Modifies |
|---|---|---|
| `src/agents/strategist/stance_schema.py` | `TickerStance` Pydantic model — three verbs, asymmetric weight rules, salvage gate for empty `update` | Rewrites the verb literal type, validator match block, salvage logic |
| `src/agents/strategist/position_thesis.py` | `PositionThesis` Pydantic model — prose thesis carrier, no numerical commitment fields | Drops `target_price`, `stop_price`, `horizon` |
| `src/agents/strategist/derivation.py` | `derive_decision_fields` — maps stance list to per-ticker order targets and sell-reason dict | Rewrites verb-dispatch match block; replaces `close_reasons` + `trim_reasons` with single `sell_reasons` dict |
| `src/agents/strategist/enricher.py` | Wires derived fields into the strategist's `StrategistDecision` output | Adapts to new derived-field names |
| `src/agents/strategist/schema.py` | `StrategistDecision` Pydantic model — output shape | Drops `trim_reasons`; renames `close_reasons` → `sell_reasons` |
| `src/agents/strategist/prompts.py` | LLM instruction template — verb table, output contract, JSON example, selective-output rule | Wholesale rewrite of the verb table and the "Your Job" section |
| `src/agents/strategist/context_shim.py` | Renders strategist's prompt-slot data (`temp:held_positions_view`, `temp:active_stances_initialised`) | New thesis-staleness rendering; drop horizon/target/stop columns; new first-tick flag |
| `src/agents/executor/_verb_dispatch.py` | `apply_stance_to_thesis` — converts a buy stance + fill price into a `PositionThesis` row | Drops handling of horizon/target/stop; reads only rationale + catalyst |
| `src/agents/executor/agent.py` | Order execution + trade-log writeback | Finds `intent="buy"` stance instead of `intent="open"`; sell-path handles full-vs-partial via broker-reported remaining qty (already correct) |
| `src/agents/risk_gate/agent.py` | Single-ticker, per-tick-delta, turnover clamps | New 5 %-per-buy clamp; drop any horizon/target/stop validation |
| `src/orchestrator/persistence.py` | `TickerStanceRow` SQLAlchemy mapping | Drop `horizon` / `target_price` / `stop_price` columns; `intent` column accepts new verb literals |
| `config/risk_gate.json` | Risk gate config | Add `max_buy_delta_per_trade: 0.05` |
| `config/README.md` | Config documentation | Document the new field |

Plus the matching test files under `tests/unit/agents/strategist/`, `tests/executor/`, `tests/unit/agents/risk_gate/`, `tests/unit/orchestrator/`.

---

## The New Stance Vocabulary (reference)

```
Verb     What it does                Required                          Optional      Forbidden
─────    ─────────────────           ────────                          ─────────     ──────────
buy      enter or increase           ticker, intent, weight, rationale  catalyst      reason, others
                                     (0 < weight ≤ 0.05)
sell     reduce or full close        ticker, intent, reason             weight        rationale, others
                                                                       (0 < w ≤ 1.0
                                                                        — full close
                                                                        if absent)
update   revise prose thesis only    ticker, intent, reason             —             weight, rationale, others
```

**Selective output rule:** On the first tick of a window the strategist emits one stance for every watchlist ticker (baseline). On every subsequent tick, emit a stance only when one of these is true: (a) a buy/sell decision, (b) the thesis prose materially changed, (c) conviction shifted enough to warrant an `update` even with no trade. Otherwise: omit the ticker — silence means "no change to my last stated view."

---

## Task 1: Pre-flight green baseline

**Files:**
- Read: `src/agents/strategist/stance_schema.py`, `src/agents/strategist/derivation.py`, `src/agents/strategist/prompts.py`, `src/agents/strategist/context_shim.py`, `src/agents/strategist/position_thesis.py`, `src/agents/executor/agent.py`, `src/agents/executor/_verb_dispatch.py`, `src/agents/risk_gate/agent.py`, `src/orchestrator/persistence.py`, `src/config/risk_gate.py`, `config/risk_gate.json`

- [ ] **Step 1: Confirm working tree clean**

```bash
git status
```

Expected: working tree clean (or only untracked files outside this plan's scope). If dirty, stash or commit before proceeding.

- [ ] **Step 2: Run the full test suite as the green baseline**

```bash
.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -30
```

Expected: all tests pass. Record the test count so we can sanity-check we haven't lost any at the end. If anything is failing on the baseline, fix it BEFORE starting this plan — a red baseline means we cannot tell our changes from pre-existing breakage.

- [ ] **Step 3: Create a working branch**

```bash
git checkout -b iter-3/schema-rewrite
```

- [ ] **Step 4: Read all the files listed under "Files" above** so the implementer holds the schema, derivation, executor, prompt, risk gate, and persistence in working memory before touching anything.

---

## Task 2: Rewrite `TickerStance` schema (three-verb collapse)

**Files:**
- Modify: `src/agents/strategist/stance_schema.py` (full rewrite of the model + validator)
- Test: `tests/unit/agents/strategist/test_stance_schema.py`

- [ ] **Step 1: Write the failing test for the new buy verb shape**

Append to `tests/unit/agents/strategist/test_stance_schema.py`:

```python
def test_buy_requires_ticker_weight_rationale():
    """buy stance requires ticker, weight in (0, 0.05], and rationale.

    No horizon, target_price, or stop_price required (or accepted)
    on a buy stance — those fields are removed from the new schema."""
    from agents.strategist.stance_schema import TickerStance

    # Valid minimal buy
    s = TickerStance(
        ticker="AAPL",
        intent="buy",
        weight=0.03,
        rationale="iPhone launch catalyst",
    )
    assert s.intent == "buy"
    assert s.weight == 0.03

    # Missing rationale rejected
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="rationale"):
        TickerStance(ticker="AAPL", intent="buy", weight=0.03)

    # Weight above 5 % delta cap rejected at schema level
    with pytest.raises(ValidationError, match="weight"):
        TickerStance(
            ticker="AAPL", intent="buy", weight=0.06,
            rationale="x",
        )

    # Extra forbidden fields (target_price etc.) rejected
    with pytest.raises(ValidationError, match="target_price|extra"):
        TickerStance(
            ticker="AAPL", intent="buy", weight=0.03,
            rationale="x", target_price=250.0,
        )


def test_sell_full_close_when_weight_absent():
    """sell stance with no weight is a full close.  Reason required."""
    from agents.strategist.stance_schema import TickerStance

    s = TickerStance(ticker="AAPL", intent="sell", reason="thesis invalidated")
    assert s.intent == "sell"
    assert s.weight is None  # full-close sentinel

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="reason"):
        TickerStance(ticker="AAPL", intent="sell")


def test_sell_partial_with_weight_in_unit_interval():
    """sell with weight is a partial trim.  Weight must be in (0, 1.0]."""
    from agents.strategist.stance_schema import TickerStance

    s = TickerStance(
        ticker="AAPL", intent="sell", weight=0.03,
        reason="taking partial profit",
    )
    assert s.weight == 0.03

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TickerStance(
            ticker="AAPL", intent="sell", weight=1.5,
            reason="x",
        )


def test_update_prose_only():
    """update stance carries only a reason — no weight, no rationale."""
    from agents.strategist.stance_schema import TickerStance

    s = TickerStance(
        ticker="AAPL", intent="update",
        reason="revising the AI catalyst timeline downward",
    )
    assert s.intent == "update"

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", intent="update", weight=0.03,
                     reason="x")


def test_old_verbs_rejected_with_clear_message():
    """open / add / trim / close / hold all fail with a migration hint."""
    from agents.strategist.stance_schema import TickerStance
    import pytest
    from pydantic import ValidationError

    for old in ("open", "add", "trim", "close", "hold"):
        with pytest.raises(ValidationError) as exc:
            TickerStance(ticker="AAPL", intent=old)
        # Pydantic Literal error contains the allowed values
        assert "buy" in str(exc.value) and "sell" in str(exc.value)
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_stance_schema.py -x -q -k "buy_requires or sell_full or sell_partial or update_prose or old_verbs"
```

Expected: FAIL — `TickerStance` still uses the old six-verb literal and old field requirements.

- [ ] **Step 3: Rewrite `src/agents/strategist/stance_schema.py`**

Replace the file body (preserve the module docstring header but update it for the new shape):

```python
"""TickerStance — the strategist's per-ticker decision substrate.

**Three-verb canonical form (iter-3 schema rewrite).**

The strategist emits zero or more ``TickerStance`` objects per tick — one
per ticker on which it has something to say.  Held positions with no
stance carry forward the last stated view.  On the FIRST tick of a
window the strategist must emit a stance for every watchlist ticker
(see ``StrategistContextShim`` for the first-tick flag).

Verb vocabulary
---------------
    buy    — enter a flat ticker or increase an existing position.
             Required: ticker, intent, weight (0 < w ≤ 0.05), rationale.
             Optional: catalyst.

    sell   — reduce or fully close a position.
             Required: ticker, intent, reason.
             Optional: weight (0 < w ≤ 1.0).  Absent weight ⇒ full close.

    update — revise prose thesis without trading.
             Required: ticker, intent, reason.

Field surface deliberately narrow: no horizon / target_price / stop_price.
The iter-2 audit found those were hallucinated 80 % of the time and
never consumed downstream — see docs/backtest-audits/baseline-window-
2025-09-iter-2.md, Bug #9.
"""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from config.strategist import get_strategist_config


logger = logging.getLogger(__name__)

_cfg = get_strategist_config()

# 5 % buy-delta cap is the schema-level hard ceiling — risk gate may
# clamp tighter.  Defined as a literal so Pydantic accepts it.
_MAX_BUY_DELTA = 0.05


class TickerStance(BaseModel):
    """One stance per ticker per tick — see module docstring for verb rules.

    ``extra="forbid"`` rejects stale callers passing deleted fields
    (target_price / stop_price / horizon / preferred_weight / conviction
    / close_reason / trim_reason) with a loud ``ValidationError``.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str

    intent: Literal["buy", "sell", "update"] = Field(
        description="Stance verb.  See module docstring.",
    )

    # Weight semantics depend on the verb (validator below enforces):
    #   buy   → required, 0 < w ≤ 0.05 (delta-per-trade cap)
    #   sell  → optional, 0 < w ≤ 1.0  (delta; absent = full close)
    #   update→ forbidden
    weight: float | None = Field(default=None, ge=0.0, le=1.0)

    catalyst: str | None = Field(default=None)
    rationale: str | None = Field(default=None)
    reason: str | None = Field(default=None)

    @model_validator(mode="after")
    def _require_intent_fields(self) -> TickerStance:
        """Enforce verb-conditional field contract.  See module docstring."""

        match self.intent:

            case "buy":
                missing = [
                    name for name, value in (
                        ("weight",    self.weight),
                        ("rationale", self.rationale),
                    )
                    if value is None
                ]
                if missing:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='buy' but is "
                        f"missing required fields: {missing}.  buy requires "
                        f"weight (0 < w ≤ {_MAX_BUY_DELTA}) and rationale."
                    )
                if self.weight is not None and (
                    self.weight <= 0.0 or self.weight > _MAX_BUY_DELTA
                ):
                    raise ValueError(
                        f"Stance for {self.ticker!r}: buy weight {self.weight} "
                        f"is outside the allowed range (0, {_MAX_BUY_DELTA}]. "
                        f"5 % is the per-trade delta cap; the risk gate may "
                        f"clamp tighter."
                    )
                if self.reason is not None:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: 'reason' is forbidden on "
                        f"buy — use 'rationale' for the entry thesis."
                    )

            case "sell":
                if self.reason is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='sell' but "
                        f"reason is missing — document why."
                    )
                if self.weight is not None and self.weight <= 0.0:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: sell weight must be > 0 "
                        f"(or absent for a full close)."
                    )
                if self.rationale is not None:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: 'rationale' is forbidden "
                        f"on sell — use 'reason'."
                    )

            case "update":
                if self.reason is None:
                    raise ValueError(
                        f"Stance for {self.ticker!r} has intent='update' but "
                        f"reason is missing — update requires prose."
                    )
                forbidden = [
                    name for name, value in (
                        ("weight",    self.weight),
                        ("rationale", self.rationale),
                        ("catalyst",  self.catalyst),
                    )
                    if value is not None
                ]
                if forbidden:
                    raise ValueError(
                        f"Stance for {self.ticker!r}: update accepts only "
                        f"'reason'; forbidden fields present: {forbidden}."
                    )

        return self
```

- [ ] **Step 4: Run the tests — verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_stance_schema.py -x -q
```

Expected: the new tests pass. Pre-existing tests in this file that reference old verbs (`open`, `add`, etc.) will fail — that is expected and they get rewritten in Task 11. For now, scope the pytest run to the new test names with `-k`.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/stance_schema.py tests/unit/agents/strategist/test_stance_schema.py
git commit -m "feat(strategist): collapse stance verbs to buy/sell/update

Drops target_price/stop_price/horizon fields and the six-verb table
(open/add/trim/close/hold/update) in favour of three verbs: buy, sell,
update.  Buy is delta-capped at 5 % per trade; sell is delta-or-full-
close (uncapped beyond current position); update is prose-only.

Motivated by iter-2 audit Bug #9 — target/stop hallucinated 80 % of
the time and never consumed downstream."
```

---

## Task 3: Drop fields from `PositionThesis`

**Files:**
- Modify: `src/agents/strategist/position_thesis.py:97-111` (drop `target_price`, `stop_price`, `horizon`)
- Test: `tests/unit/agents/strategist/test_position_thesis.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/strategist/test_position_thesis.py`:

```python
def test_position_thesis_has_no_horizon_target_stop():
    """PositionThesis after iter-3 carries only prose + opened context."""
    from agents.strategist.position_thesis import PositionThesis

    # These fields must not exist on the model
    fields = set(PositionThesis.model_fields.keys())
    assert "target_price" not in fields
    assert "stop_price" not in fields
    assert "horizon" not in fields

    # Required fields that DO exist
    assert "rationale" in fields
    assert "opened_price" in fields
    assert "opened_at" in fields


def test_extra_field_target_price_rejected():
    """Stale callers passing target_price get a loud ValidationError."""
    from agents.strategist.position_thesis import PositionThesis
    from datetime import datetime, timezone

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PositionThesis(
            ticker="AAPL",
            opened_at=datetime.now(timezone.utc),
            opened_price=100.0,
            rationale="x",
            target_price=120.0,
        )
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_position_thesis.py -x -q -k "no_horizon or target_price_rejected"
```

Expected: FAIL — `PositionThesis` still has `target_price`/`stop_price`/`horizon`.

- [ ] **Step 3: Delete the three field declarations**

Edit `src/agents/strategist/position_thesis.py`:

- Delete the `target_price: float | None = Field(...)` block (around lines 97-100)
- Delete the `stop_price: float | None = Field(...)` block (around lines 101-108)
- Delete the `horizon: Literal[...] = Field(...)` block (around lines 109-111)
- Update the module/class docstring at line 50 to drop `target_price`, `stop_price`, `horizon` from the example field list
- Ensure `ConfigDict(extra="forbid")` is set on the class — if not already, add it

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_position_thesis.py -x -q
```

Expected: the two new tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/position_thesis.py tests/unit/agents/strategist/test_position_thesis.py
git commit -m "refactor(strategist): drop target/stop/horizon from PositionThesis

Thesis is now prose-only: rationale, catalyst, opened_price, opened_at.
Matches the iter-3 stance schema."
```

---

## Task 4: Rewrite `derive_decision_fields`

**Files:**
- Modify: `src/agents/strategist/derivation.py:156-360` (rewrite Pass 1 verb dispatch + replace `close_reasons`/`trim_reasons` with `sell_reasons`)
- Modify: `src/agents/strategist/schema.py` (rename `close_reasons` → `sell_reasons`; drop `trim_reasons`)
- Test: `tests/unit/agents/strategist/test_derivation.py`

- [ ] **Step 1: Write the failing test for buy/sell/update dispatch**

Append to `tests/unit/agents/strategist/test_derivation.py`:

```python
def test_derivation_dispatches_buy_to_target_weight():
    """A buy stance writes the delta into target_weights additively."""
    from agents.strategist.derivation import derive_decision_fields
    from agents.strategist.stance_schema import TickerStance
    from agents.strategist.derivation import TickContext

    ctx = TickContext(
        watchlist=["AAPL", "MSFT"],
        held_tickers=set(),  # AAPL is flat
        current_weights={"AAPL": 0.0, "MSFT": 0.0},
    )
    stances = [TickerStance(
        ticker="AAPL", intent="buy", weight=0.03,
        rationale="iPhone launch catalyst",
    )]
    derived = derive_decision_fields(stances, ctx)
    # buy delta = 0.03 from flat → new weight is 0.03
    assert derived.target_weights["AAPL"] == 0.03
    assert "AAPL" not in derived.sell_reasons


def test_derivation_dispatches_sell_full_close():
    """A sell stance with no weight is a full close — target_weight = 0."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(
        watchlist=["AAPL"],
        held_tickers={"AAPL"},
        current_weights={"AAPL": 0.08},
    )
    stances = [TickerStance(
        ticker="AAPL", intent="sell",
        reason="thesis invalidated",
    )]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.0
    assert derived.sell_reasons["AAPL"] == "thesis invalidated"


def test_derivation_dispatches_sell_partial():
    """A sell stance with weight=0.03 reduces current weight by 0.03."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(
        watchlist=["AAPL"],
        held_tickers={"AAPL"},
        current_weights={"AAPL": 0.08},
    )
    stances = [TickerStance(
        ticker="AAPL", intent="sell", weight=0.03,
        reason="trimming on overbought",
    )]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.05
    assert derived.sell_reasons["AAPL"] == "trimming on overbought"


def test_derivation_update_does_not_change_weight():
    """An update stance carries forward the current weight unchanged."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(
        watchlist=["AAPL"],
        held_tickers={"AAPL"},
        current_weights={"AAPL": 0.08},
    )
    stances = [TickerStance(
        ticker="AAPL", intent="update",
        reason="revising AI catalyst timeline downward but still holding",
    )]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.08
    assert "AAPL" not in derived.sell_reasons


def test_derivation_held_omission_carries_weight_forward():
    """A held ticker with no stance keeps its current weight (implicit hold)."""
    from agents.strategist.derivation import derive_decision_fields, TickContext

    ctx = TickContext(
        watchlist=["AAPL", "MSFT"],
        held_tickers={"AAPL", "MSFT"},
        current_weights={"AAPL": 0.05, "MSFT": 0.07},
    )
    derived = derive_decision_fields([], ctx)  # silence on both
    assert derived.target_weights["AAPL"] == 0.05
    assert derived.target_weights["MSFT"] == 0.07
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_derivation.py -x -q -k "buy_to_target or sell_full or sell_partial or update_does_not or held_omission"
```

Expected: FAIL — old derivation logic still expects open/add/trim/close/hold/update and raises `StrategistContractViolation` on held-omission.

- [ ] **Step 3: Rewrite `derive_decision_fields`**

In `src/agents/strategist/derivation.py`, replace the Pass 1 match block (around lines 244-340) with:

```python
    # Pass 1 — interpret each emitted stance against its verb's contract.
    for stance in stances:
        emitted.add(stance.ticker)

        if stance.intent is None:
            raise StrategistContractViolation(
                f"Stance for {stance.ticker!r} has intent=None.  Every stance "
                f"must carry an explicit intent (buy / sell / update)."
            )

        current = ctx.current_weights.get(stance.ticker, 0.0)

        match stance.intent:

            case "buy":
                # weight is the DELTA — increase current position by that much.
                target_weights[stance.ticker] = current + stance.weight

            case "sell":
                # weight absent ⇒ full close; weight present ⇒ reduce by delta
                # (clamped to current; risk gate will surface clamps as audit).
                if stance.weight is None:
                    target_weights[stance.ticker] = 0.0
                else:
                    target_weights[stance.ticker] = max(0.0, current - stance.weight)
                sell_reasons[stance.ticker] = stance.reason

            case "update":
                # No trade — current weight carries forward verbatim.  Reason
                # is captured separately for the trace; not surfaced in
                # target_weights or sell_reasons.
                target_weights[stance.ticker] = current
                update_reasons[stance.ticker] = stance.reason
```

Adjust the function signature / return type:
- `DerivedFields` in `schema.py` gains a new `sell_reasons: dict[str, str]` and `update_reasons: dict[str, str]` field
- Drop `close_reasons` and `trim_reasons` from `DerivedFields`
- The Pass 2 carry-forward block stays — silent omission of a HELD ticker now carries its current weight (no `StrategistContractViolation`) because implicit hold is now valid

Update the module docstring to reflect the new active-stances rule: held tickers MAY be omitted (carry-forward), flat tickers must be present in `emitted` set only if the strategist wants to buy them.

- [ ] **Step 4: Run the tests — verify they pass**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_derivation.py -x -q
```

Expected: the five new tests pass. Old tests that asserted `close_reasons` / `trim_reasons` or `StrategistContractViolation` on held-omission will fail; they get rewritten in Task 11.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/derivation.py src/agents/strategist/schema.py tests/unit/agents/strategist/test_derivation.py
git commit -m "refactor(strategist): derive_decision_fields for three-verb schema

buy is additive delta; sell is reductive (full close if no weight);
update carries weight forward.  close_reasons/trim_reasons collapsed
into sell_reasons; new update_reasons dict.  Held-ticker omission is
now valid (implicit hold by silence)."
```

---

## Task 5: Update strategist enricher to consume new derived fields

**Files:**
- Modify: `src/agents/strategist/enricher.py:218` and downstream wiring
- Test: existing enricher tests will surface the rename

- [ ] **Step 1: Run existing enricher tests to see what breaks**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_enricher.py -x -q
```

Expected: FAIL — references to `close_reasons` / `trim_reasons` no longer exist on `DerivedFields`.

- [ ] **Step 2: Update enricher**

In `src/agents/strategist/enricher.py`, find every reference to `derived.close_reasons` and `derived.trim_reasons`. Replace with `derived.sell_reasons` (the single replacement dict). Drop the `trim_reasons` field from `StrategistDecision` if it was being assembled there.

- [ ] **Step 3: Update existing enricher tests**

Rename assertions from `decision.close_reasons` / `decision.trim_reasons` to `decision.sell_reasons`. Drop any test that exercised the open/add/trim/close/hold verb explicitly — those scenarios become buy/sell/update and the assertions need rewording.

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_enricher.py -x -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/enricher.py tests/unit/agents/strategist/test_enricher.py
git commit -m "refactor(strategist): enricher consumes sell_reasons from new derivation"
```

---

## Task 6: Update `TickerStanceRow` persistence + apply_stance_to_thesis

**Files:**
- Modify: `src/orchestrator/persistence.py:120-200` — drop `horizon`/`target_price`/`stop_price` columns from `TickerStanceRow`; update `save_strategist_decision` row construction
- Modify: `src/agents/executor/_verb_dispatch.py` — `apply_stance_to_thesis` no longer reads horizon/target/stop from the stance
- Test: `tests/unit/orchestrator/test_persistence.py` and `tests/unit/agents/executor/test_verb_dispatch.py`

- [ ] **Step 1: Write the failing test for verb dispatch**

In `tests/unit/agents/executor/test_verb_dispatch.py` (create the file if it doesn't exist; otherwise append):

```python
def test_apply_stance_to_thesis_buy_only_reads_rationale_and_catalyst():
    """apply_stance_to_thesis on a buy stance produces a thesis with rationale
    + catalyst, no horizon/target/stop fields."""
    from datetime import datetime, timezone
    from agents.executor._verb_dispatch import apply_stance_to_thesis
    from agents.strategist.stance_schema import TickerStance

    stance = TickerStance(
        ticker="AAPL", intent="buy", weight=0.03,
        rationale="iPhone launch", catalyst="iPhone 17 launch event",
    )
    thesis = apply_stance_to_thesis(
        stance, prior_row=None, fill_price=210.0,
        tick_id="tick-1", as_of=datetime.now(timezone.utc),
    )
    assert thesis.rationale == "iPhone launch"
    assert thesis.catalyst == "iPhone 17 launch event"
    assert thesis.opened_price == 210.0
    # These fields no longer exist on PositionThesis
    assert not hasattr(thesis, "horizon")
    assert not hasattr(thesis, "target_price")
    assert not hasattr(thesis, "stop_price")
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/python -m pytest tests/unit/agents/executor/test_verb_dispatch.py -x -q -k "buy_only_reads"
```

Expected: FAIL — current `apply_stance_to_thesis` references stance.horizon/target_price/stop_price.

- [ ] **Step 3: Update `apply_stance_to_thesis`**

In `src/agents/executor/_verb_dispatch.py`, drop any `stance.horizon`, `stance.target_price`, `stance.stop_price` reads. The function should only consume `stance.ticker`, `stance.weight`, `stance.rationale`, `stance.catalyst` plus the fill metadata. Also: change the `stance.intent == "open"` filter to `stance.intent == "buy"`.

- [ ] **Step 4: Update `TickerStanceRow` and `save_strategist_decision`**

In `src/orchestrator/persistence.py`:
- Drop the `horizon`, `target_price`, `stop_price` `mapped_column` declarations on `TickerStanceRow` (lines 135-137).
- In the row factory `save_strategist_decision`, drop the corresponding `horizon=...`, `target_price=...`, `stop_price=...` kwargs (lines 193-195).
- The `horizon_intent` column on `trade_log_row` (line 96) — discuss: this records the original entry horizon for analytics. With horizon removed from the stance, this column becomes ungrounded. **Drop it** in the same pass; the trade-log row should carry `opened_rationale` only.

- [ ] **Step 5: Run all persistence + verb-dispatch tests**

```bash
.venv/bin/python -m pytest tests/unit/orchestrator/test_persistence.py tests/unit/agents/executor/test_verb_dispatch.py -x -q
```

Expected: the new test passes. Existing tests that referenced `horizon`/`target_price`/`stop_price` need updating — fix them inline, then re-run.

- [ ] **Step 6: Commit**

```bash
git add src/agents/executor/_verb_dispatch.py src/orchestrator/persistence.py tests/unit/agents/executor/test_verb_dispatch.py tests/unit/orchestrator/test_persistence.py
git commit -m "refactor(executor,persistence): drop horizon/target/stop from thesis row

apply_stance_to_thesis reads only rationale + catalyst.  TickerStanceRow
and trade_log_row drop the three numerical columns.  intent='buy' is
the new filter (was 'open')."
```

---

## Task 7: Update executor agent's BUY-stance finder

**Files:**
- Modify: `src/agents/executor/agent.py:129-184` (the BUY branch)
- Test: `tests/executor/test_executor.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/executor/test_executor.py`:

```python
async def test_executor_buy_path_finds_buy_intent_stance(monkeypatch, fake_broker_filled):
    """Executor's BUY branch must look for intent='buy', not intent='open'."""
    # Setup: state contains a stance list with one intent='buy' stance for AAPL
    # and an order with action='BUY' for AAPL.  Drive the executor and assert
    # the resulting positions dict has AAPL with the buy's rationale.
    from agents.executor.agent import ExecutorAgent

    state = {
        "strategist_decision": {
            "stances": [{
                "ticker": "AAPL", "intent": "buy", "weight": 0.03,
                "rationale": "iPhone launch catalyst",
            }],
            "decision_tag": "test_decision",
        },
        "orders": [{"ticker": "AAPL", "action": "BUY", "quantity": 10,
                    "est_price": 210.0}],
        "as_of": "2026-01-15T13:30:00+00:00",
        "tick_id": "tick-1",
        "user:positions": {},
    }
    agent = ExecutorAgent(broker=fake_broker_filled, db_session=None)
    # ... invoke agent.run(state) ...
    # assert state["positions"]["AAPL"]["rationale"] == "iPhone launch catalyst"
```

Note: the test scaffold above is illustrative — the real test should mirror existing executor tests' fixture pattern. The KEY assertion is that `intent="buy"` is the filter, not `intent="open"`.

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/python -m pytest tests/executor/test_executor.py -x -q -k "buy_intent"
```

Expected: FAIL — executor still filters for `intent == "open"`.

- [ ] **Step 3: Update the BUY branch**

In `src/agents/executor/agent.py:139`, change:

```python
and (s.get("intent") if isinstance(s, dict) else s.intent) == "open"
```

to:

```python
and (s.get("intent") if isinstance(s, dict) else s.intent) == "buy"
```

Also update the surrounding comments at lines 118-128 to reference "buy stance" instead of "open stance."

The SELL branch (lines 195-298) already does the right thing — it asks the broker for the remaining quantity and treats `remaining <= 0` as a full close. No change needed there.

- [ ] **Step 4: Run the tests**

```bash
.venv/bin/python -m pytest tests/executor/ -x -q
```

Expected: PASS (the new test plus existing ones, modulo fixture updates needed for old stance shapes — fix inline).

- [ ] **Step 5: Commit**

```bash
git add src/agents/executor/agent.py tests/executor/test_executor.py
git commit -m "refactor(executor): BUY path filters for intent='buy' (was 'open')"
```

---

## Task 8: Rewrite strategist prompt — verb table + output contract

**Files:**
- Modify: `src/agents/strategist/prompts.py:120-230` (Your Job section + verb table + JSON example)
- Test: `tests/unit/agents/strategist/test_prompts_v2.py`

- [ ] **Step 1: Rewrite the "Your Job" section + verb table**

Replace lines 124-170 of `src/agents/strategist/prompts.py` with:

```
## Your Job

Watchlist for this tick: {tickers}.

**First tick of a window** ({{FIRST_TICK_FLAG}}): emit one stance for
every watchlist ticker so you establish a baseline view.  This is the
only tick where output volume is mandated.

**Every subsequent tick**: emit a stance ONLY when you have something
to say — one of:

  (a) you are buying or selling (intent='buy' or 'sell'),
  (b) you are revising the thesis prose on a held position
      (intent='update'),
  (c) your conviction has shifted enough that you want the audit trail
      to reflect it (also intent='update').

Tickers you do NOT mention this tick carry forward your last stated
view.  Silence is a valid response and means "no change."

## OUTPUT CONTRACT — every rule is enforced; violations abort the tick

| Intent  | What it means                              | Required                         | Optional   |
|---------|--------------------------------------------|----------------------------------|------------|
| buy     | enter flat or increase existing position   | weight, rationale                | catalyst   |
| sell    | reduce or fully close a position           | reason                           | weight     |
| update  | revise thesis prose (no trade)             | reason                           | —          |

**Weight semantics:**

- ``buy`` weight is the DELTA — how much to increase the position by,
  as a fraction of portfolio (e.g. 0.03 = 3 %).  Hard schema cap:
  weight ≤ {{MAX_BUY_DELTA_PCT}} % per trade.  To build a larger
  position, buy across multiple ticks.
- ``sell`` weight is the DELTA — how much to reduce by.  Omit the
  weight for a full close.  Sell is uncapped beyond the current
  position size (you cannot sell more than you hold).
- ``update`` takes no weight — no trade happens.

**Forbidden fields by verb** (the schema rejects, the tick aborts):

- buy:    no ``reason``    (use ``rationale``)
- sell:   no ``rationale`` (use ``reason``)
- update: no ``weight``, no ``rationale``, no ``catalyst``
- ALL verbs: no ``target_price``, ``stop_price``, ``horizon`` — those
  fields no longer exist in the schema.  Your thesis prose carries
  your view; numerical commitments are not required.

**Choosing between sell and update:** if you want to exit (or trim)
the position this tick, use ``sell``.  If you want to revise what you
think but keep holding, use ``update``.  Holding silently (omitting
the ticker) is also valid if your view truly has not changed.
```

- [ ] **Step 2: Rewrite the JSON example block (lines 211-230)**

```
## How to submit your output

Emit ONE JSON object with this exact shape — nothing else.  Examples
of all three verbs shown; in practice you will often emit zero or one
stance per tick (after the first).

{{
  "stances": [
    {{
      "ticker": "<ticker>", "intent": "buy",
      "weight": <0.0-{{MAX_BUY_DELTA}}>,
      "rationale": "<one short sentence>",
      "catalyst": "<short phrase, optional>"
    }},
    {{
      "ticker": "<ticker>", "intent": "sell",
      "reason": "<what changed, one sentence>"
    }},
    {{
      "ticker": "<ticker>", "intent": "update",
      "reason": "<thesis revision, one sentence>"
    }}
  ],
  "decision_tag": "<snake_case_label>",
  "confidence": <0.0-1.0>,
  "reasoning": "<brief>",
  "thesis": "<optional prose; null carries the prior thesis forward>"
}}
```

- [ ] **Step 3: Drop the field-constraints section's references to removed fields**

In `src/agents/strategist/prompts.py:181-208` (`### Field constraints`), delete the bullets for `horizon`, `target_price`, `stop_price`. Update the `weight` bullet to reflect the new asymmetric rule (buy capped at 5 %, sell uncapped delta). Keep bullets for `rationale`, `reason`, `catalyst`, `confidence`, `reasoning`, `thesis`, `decision_tag`.

- [ ] **Step 4: Update placeholder substitution at the bottom of the file**

In `src/agents/strategist/prompts.py:251-256`, ensure the new placeholders `{{MAX_BUY_DELTA_PCT}}`, `{{MAX_BUY_DELTA}}`, `{{FIRST_TICK_FLAG}}` are substituted. Drop substitutions for placeholders that no longer appear in the template (`{{MAX_DELTA_PCT}}`, `{{MAX_TURNOVER_PCT}}` if removed).

- [ ] **Step 5: Update prompt-template tests**

In `tests/unit/agents/strategist/test_prompts_v2.py`, drop the assertions that the prompt mentions `target_price` / `stop_price` / `horizon` / `open` / `add` / `trim` / `close` / `hold`. Add assertions:
- The prompt contains the new three-verb table.
- The prompt contains the selective-output rule.
- `{{MAX_BUY_DELTA}}` substitutes to `0.05`.

- [ ] **Step 6: Run the prompt tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_prompts_v2.py -x -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/prompts.py tests/unit/agents/strategist/test_prompts_v2.py
git commit -m "feat(strategist): three-verb prompt + selective-output rule

Verb table collapses to buy/sell/update.  First tick of a window
requires a stance for every ticker (baseline establishment);
subsequent ticks emit only when something changed — silence = hold.
Drops all guidance for target_price/stop_price/horizon."
```

---

## Task 9: Update `context_shim` — selective-output + thesis-staleness

**Files:**
- Modify: `src/agents/strategist/context_shim.py` — render `temp:active_stances_initialised` flag, thesis-staleness column in `temp:held_positions_view`, drop horizon/target/stop columns
- Test: `tests/unit/agents/strategist/test_context_shim.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/strategist/test_context_shim.py`:

```python
def test_first_tick_sets_initialised_flag_false():
    """On the first tick (no prior stances), active_stances_initialised is False
    — the prompt uses this to render the 'first tick' instruction."""
    from agents.strategist.context_shim import StrategistContextShim

    state = {"user:positions": {}, "user:active_stances_initialised": False}
    shim = StrategistContextShim()
    rendered = shim.render(state)
    assert rendered["temp:active_stances_initialised"] == "False"


def test_held_view_shows_thesis_staleness():
    """Held positions view shows how many ticks since the thesis last updated."""
    from agents.strategist.context_shim import StrategistContextShim

    state = {
        "user:positions": {
            "AAPL": {
                "rationale": "iPhone launch",
                "opened_price": 210.0,
                "opened_at": "2026-01-15T13:30:00+00:00",
                "thesis_last_updated_tick": 1,
            }
        },
        "user:current_tick_index": 5,  # 4 ticks since update
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)
    held = rendered["temp:held_positions_view"]
    assert "AAPL" in held
    assert "4 ticks" in held or "stale" in held.lower()


def test_held_view_omits_horizon_target_stop():
    """Held view must not mention horizon/target_price/stop_price — those
    fields no longer exist on the thesis."""
    from agents.strategist.context_shim import StrategistContextShim

    state = {
        "user:positions": {
            "AAPL": {
                "rationale": "iPhone launch",
                "opened_price": 210.0,
                "opened_at": "2026-01-15T13:30:00+00:00",
            }
        },
        "user:current_tick_index": 1,
    }
    shim = StrategistContextShim()
    rendered = shim.render(state)
    held = rendered["temp:held_positions_view"]
    assert "horizon" not in held.lower()
    assert "target" not in held.lower()
    assert "stop" not in held.lower()
```

- [ ] **Step 2: Run the failing tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -x -q -k "first_tick or thesis_staleness or omits_horizon"
```

Expected: FAIL.

- [ ] **Step 3: Update `context_shim.py`**

- Add a new state-delta key `temp:active_stances_initialised` rendered from `state.get("user:active_stances_initialised", False)` — booleanised to the string "True"/"False" for the prompt template.
- Add a `_render_held_positions_view` change: format each held position as a multi-line block with `ticker`, `rationale`, `opened at $price on date`, `catalyst (if any)`, and `thesis last updated N ticks ago`. Drop any rendering of `horizon`, `target_price`, `stop_price`.
- Compute the staleness number using `state["user:current_tick_index"] - thesis["thesis_last_updated_tick"]` (default 0 if either is missing).
- The executor's after-callback (Task 6 territory) should also set `thesis_last_updated_tick = current_tick_index` when writing the thesis. If that's not already there, add it in this task.

- [ ] **Step 4: Update the strategist's after-callback to set `user:active_stances_initialised = True`**

Find the strategist's after-callback (likely in `src/agents/strategist/agent.py` or in `enricher.py`) that runs after a successful tick. Add a state-delta entry setting `user:active_stances_initialised = True`. This is a one-shot flag — once set, the prompt switches to selective-output mode.

- [ ] **Step 5: Run the tests**

```bash
.venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -x -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/context_shim.py src/agents/strategist/agent.py tests/unit/agents/strategist/test_context_shim.py
git commit -m "feat(strategist): selective-output flag + thesis staleness in held view

temp:active_stances_initialised gates the first-tick-vs-rest rendering
in the prompt.  Held positions view shows thesis staleness in ticks
since the last update, and no longer renders horizon/target/stop —
those fields are gone."
```

---

## Task 10: Update risk gate — 5 % buy-delta clamp

**Files:**
- Modify: `src/agents/risk_gate/agent.py` (add buy-delta clamp; drop any horizon/target/stop validation)
- Modify: `config/risk_gate.json` (add `max_buy_delta_per_trade: 0.05`)
- Modify: `src/config/risk_gate.py` (add the new config field)
- Modify: `config/README.md` (document the new field)
- Test: `tests/unit/agents/risk_gate/test_agent.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/agents/risk_gate/test_agent.py`:

```python
def test_buy_delta_above_5pct_is_clamped():
    """A buy stance with weight > max_buy_delta_per_trade is clamped to the cap
    and an audit clamp is emitted."""
    from agents.risk_gate.agent import RiskGate
    from agents.strategist.stance_schema import TickerStance

    # Note: the schema itself rejects buy weight > 0.05.  The risk gate
    # still needs to enforce this against derived target_weights in case
    # a downstream path mutates them.  The test scenario uses a stance
    # at exactly 0.05 and a downstream delta calculation that exceeds it.
    rg = RiskGate()
    stances = [TickerStance(
        ticker="AAPL", intent="buy", weight=0.05,
        rationale="test",
    )]
    current_weights = {"AAPL": 0.0}
    clamped, clamps = rg.apply(stances, current_weights)
    # Stance at exactly the cap should pass unchanged.
    assert clamped[0].weight == 0.05
    assert not any(c.reason == "buy_delta_exceeded" for c in clamps)

    # Now construct a stance that would put total > position cap (20 %)
    # — that's a different clamp (total position cap), still must fire.
    stances = [TickerStance(
        ticker="AAPL", intent="buy", weight=0.05,
        rationale="test",
    )]
    current_weights = {"AAPL": 0.18}  # already 18 %, +5 % = 23 % > 20 %
    clamped, clamps = rg.apply(stances, current_weights)
    assert any(c.reason == "position_cap_exceeded" for c in clamps)
```

- [ ] **Step 2: Run the failing test**

```bash
.venv/bin/python -m pytest tests/unit/agents/risk_gate/test_agent.py -x -q -k "buy_delta_above"
```

Expected: FAIL — risk gate doesn't have the buy-delta clamp.

- [ ] **Step 3: Add the config field**

In `config/risk_gate.json`, add:

```json
{
  ...,
  "max_buy_delta_per_trade": 0.05
}
```

In `src/config/risk_gate.py`, add the matching Pydantic field on the config model:

```python
max_buy_delta_per_trade: float = Field(default=0.05, gt=0.0, le=1.0,
    description="Per-buy delta cap (fraction of portfolio).  Applied "
                "in addition to the schema-level 5 % cap on TickerStance.")
```

In `config/README.md`, add a line under the risk-gate section documenting the field.

- [ ] **Step 4: Add the clamp in the risk gate**

In `src/agents/risk_gate/agent.py`, in the per-stance loop, add a check: if `stance.intent == "buy"` and `stance.weight > config.max_buy_delta_per_trade`, clamp `stance.weight = config.max_buy_delta_per_trade` and emit a `RiskClamp(reason="buy_delta_exceeded", ...)`. Also drop any code that referenced `stance.horizon`, `stance.target_price`, `stance.stop_price`.

The existing position-cap, turnover, and cash-buffer clamps stay — they operate on `target_weights`, not stance fields.

- [ ] **Step 5: Run the test**

```bash
.venv/bin/python -m pytest tests/unit/agents/risk_gate/test_agent.py -x -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agents/risk_gate/agent.py config/risk_gate.json src/config/risk_gate.py config/README.md tests/unit/agents/risk_gate/test_agent.py
git commit -m "feat(risk-gate): 5 % per-buy delta clamp + drop horizon/target/stop refs"
```

---

## Task 11: Sweep test fixtures + integration tests

**Files:**
- Modify: all test files under `tests/` that use the old stance shapes
- Test: full suite

- [ ] **Step 1: Run the full suite to enumerate breakage**

```bash
.venv/bin/python -m pytest tests/ -x -q 2>&1 | tail -100
```

Expected: a finite list of failures, all of which fall into one of these buckets:

1. Test fixtures referencing old verbs (`open` / `add` / `trim` / `close` / `hold`) → rename to `buy` / `sell` / `update`
2. Test fixtures setting `target_price` / `stop_price` / `horizon` on a stance → drop those kwargs
3. Tests asserting `close_reasons` / `trim_reasons` on a decision → rename to `sell_reasons`
4. Tests asserting `derive_decision_fields` raises `StrategistContractViolation` on held-omission → flip to assert it carries forward

- [ ] **Step 2: Fix the failures bucket by bucket**

For each failing file, work through the four buckets above and apply the rename. Use one commit per file group (e.g. all stance-schema test edits in one commit; all derivation test edits in another). Suggested file groups:

- `tests/unit/agents/strategist/test_stance_schema.py` — already updated in Task 2; sanity-check it's still green
- `tests/unit/agents/strategist/test_derivation.py` — already updated in Task 4
- `tests/unit/agents/strategist/test_enricher.py` — already updated in Task 5
- `tests/unit/agents/strategist/test_context_shim.py` — already updated in Task 9
- `tests/unit/agents/strategist/test_prompts_v2.py` — already updated in Task 8
- `tests/unit/agents/strategist/test_agent.py` — likely needs sweep
- `tests/unit/agents/risk_gate/test_agent.py` — already updated in Task 10
- `tests/unit/agents/executor/test_verb_dispatch.py` — already updated in Task 6
- `tests/executor/test_executor.py` and `test_executor_bookkeeping.py` — likely need sweep
- `tests/unit/orchestrator/test_persistence.py` — already updated in Task 6
- `tests/integration/test_strategist_executor.py` (if exists) — likely needs sweep
- Backtest fixtures under `tests/unit/backtest/` if they assert decision shapes

- [ ] **Step 3: Run the full suite — green**

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: all green, same test count as the baseline from Task 1 (modulo new tests added in earlier tasks, which should net positive).

- [ ] **Step 4: Commit**

```bash
git add tests/
git commit -m "test: sweep fixtures for three-verb stance schema"
```

---

## Task 12: Integration smoke test — single tick end-to-end

**Files:**
- Test: `tests/integration/test_strategist_executor_e2e.py` (create if absent; otherwise extend)

- [ ] **Step 1: Write the integration test**

```python
async def test_single_tick_strategist_to_executor_with_three_verbs():
    """Drive one full tick: strategist emits buy + sell + update stances;
    executor processes orders; positions reflect the changes.

    No LLM call — the strategist's LLM output is stubbed with a hand-
    constructed JSON payload to keep the test deterministic."""
    # Setup: state has AAPL held at 0.05; MSFT flat.
    # Stub strategist output: [
    #   {ticker: AAPL, intent: sell, weight: 0.05, reason: "test exit"},
    #   {ticker: MSFT, intent: buy,  weight: 0.03, rationale: "test entry"},
    #   {ticker: GOOGL, intent: update, reason: "still bullish"},  # GOOGL held
    # ]
    # Drive pipeline; assert:
    #   - AAPL position closed (qty 0); user:closed_trades_log has an entry
    #   - MSFT position opened at fill price; rationale recorded
    #   - GOOGL position unchanged; thesis prose updated
```

Fill in using existing integration test scaffolding from neighbouring files.

- [ ] **Step 2: Run the test**

```bash
.venv/bin/python -m pytest tests/integration/test_strategist_executor_e2e.py -x -q
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_strategist_executor_e2e.py
git commit -m "test: integration smoke for three-verb single-tick flow"
```

---

## Task 13: Backtest smoke run

**Files:** none modified — this is a runtime check.

- [ ] **Step 1: Run a short backtest window to verify pipeline doesn't crash**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --max-ticks 4 --run-id smoke-iter3
```

Expected: completes without error. Inspect `backtests/baseline-2025-09/runs/smoke-iter3/decisions/*.json` and confirm:
- Stance objects in each decision have intents in {`buy`, `sell`, `update`} only — no `open`/`add`/`trim`/`close`/`hold`.
- No `target_price`, `stop_price`, `horizon` fields anywhere in the stance bodies.
- At least one tick shows a sell that maps to a full close (qty zeroed and `user:closed_trades_log` appended).

- [ ] **Step 2: Diff the equity curve against a recent run (sanity, not pass/fail)**

```bash
ls -la backtests/baseline-2025-09/runs/smoke-iter3/report/
```

Expected: `metrics.md` exists. Open it. Headline numbers will differ from iter-2 — the goal of this smoke check is "no crash, output shape correct," NOT "performance improved." Performance assessment is the next audit's job after a full iter-3 run.

- [ ] **Step 3: Tear down the smoke artefacts**

```bash
rm -rf backtests/baseline-2025-09/runs/smoke-iter3
```

(The smoke run was for pipeline validation; the artefact tree itself is not worth committing.)

- [ ] **Step 4: Final commit + push the branch**

```bash
git log --oneline iter-3/schema-rewrite ^main
```

Expected: a clean sequence of ~13 commits, one per task. Do NOT push unless the user confirms — leave the branch local for the user to inspect and run a full iter-3 backtest from.

---

## Task 14: Cleanup

**Files:**
- Search for any remaining references to dropped verbs / fields anywhere in `src/` and `tests/`

- [ ] **Step 1: Grep for stragglers**

```bash
grep -rn '"open"\|"add"\|"trim"\|"close"\|"hold"' src/agents/strategist/ src/agents/executor/ src/agents/risk_gate/ | grep -v 'test\|\.pyc' | head -30
grep -rn 'target_price\|stop_price\|horizon' src/agents/strategist/ src/agents/executor/ src/agents/risk_gate/ src/orchestrator/persistence.py | head -30
```

Expected: any hit is either (a) inside a comment / docstring explaining the migration, or (b) a real reference that needs fixing. Triage and fix.

- [ ] **Step 2: Update CLAUDE.md if it documents the old verb set**

```bash
grep -n "open\|add\|trim\|close\|hold\|target_price\|stop_price\|horizon" .claude/CLAUDE.md
```

If any project-level CLAUDE.md text describes the old stance vocabulary, update it. The user-global CLAUDE.md (`~/.claude/CLAUDE.md`) is out of scope.

- [ ] **Step 3: Final test sweep**

```bash
.venv/bin/python -m pytest tests/ -q
```

Expected: green.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "chore: remove stragglers from old stance vocabulary"
```

---

## Out of scope — explicitly deferred

These were discussed during planning but deliberately excluded from this iteration:

- **Deterministic max-drawdown floor in the risk gate.** Decision: ship the cleaner schema first; the user wants to see whether the hair-trigger exits subside before adding the floor. Revisit in iter-4 if the iter-3 backtest still shows premature closes.
- **Bug #8 (holding-period anchor in the prompt).** The prompt rewrite in Task 8 already drops references to `horizon`. A separate prompt-discipline addition (e.g. "thesis invalidation requires evidence that opposes the *original* catalyst, not just opposing intra-window signals") is the right place to add invalidation-bar guidance. This is deferred to a follow-on prompt-tuning pass; it does not block the schema rewrite.
- **Bug #11 (re-entry discipline rule).** Same reason as above — prompt-tuning.
- **Bug #15 (MA50/MA200 levels + ATR-stop suggestion + support/resistance).** New feature compute; needs its own spec.
- **The five mechanical fixes** (Bugs #12, #13, #14, #15a, #16b) — these are independent of the schema rewrite and can ship in parallel via subagents in a separate session, as discussed.

---

## Acceptance criteria

Iter-3 is done when ALL of the following hold:

1. `git diff main...iter-3/schema-rewrite` touches the files in the File Structure table above, plus the corresponding test files. No surprise modifications.
2. `.venv/bin/python -m pytest tests/ -q` is green, with at least the same test count as the Task 1 baseline (new tests added in tasks 2-10 should net positive).
3. The smoke backtest run in Task 13 completes without error and produces decision JSON with only the new verb shapes.
4. No `grep` hit for `target_price` / `stop_price` / `horizon` in `src/agents/strategist/`, `src/agents/executor/`, `src/agents/risk_gate/`, `src/orchestrator/persistence.py` outside of migration comments.
5. No `grep` hit for stance intents `"open"` / `"add"` / `"trim"` / `"close"` / `"hold"` in the same dirs outside of migration comments.

When all five hold, the user can run a full iter-3 backtest (`scripts.backtest_run --window baseline-2025-09`) and the result feeds the iter-3 audit.

---

## Rollback

If the iter-3 backtest shows materially worse performance than iter-2 (>5 pp deterioration vs SPY, or catastrophic single-position losses), the rollback is:

```bash
git checkout main
git branch -D iter-3/schema-rewrite   # only after manual review of what's lost
```

No DB migration to reverse (pre-deployment). No prod traffic affected. The iter-2 git SHA (206e8c6) remains the last-known-good for the strategist.

If only specific tasks regressed, individual commits can be reverted via `git revert <sha>` — the per-task commit structure makes this surgical.
