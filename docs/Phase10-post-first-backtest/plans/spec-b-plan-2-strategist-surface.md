# Spec B Plan 2 — Strategist Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reshape the strategist's prompt surface so that tick *N* is *structurally* different from tick 1 — cold-start vs incremental mode framing, an evolution-aware held-view, and a required stance per held position — and prove it with unit tests plus a 5-tick prompt-diversity backtest.

**Architecture:** Three bands threaded in dependency order — (1) the `temp:strategist_mode` injection + held-view rewrite (the prompt-surface layer), (2) the prompt-template + Output-Requirements update + carry-forward removal in `derivation.py` (the LLM-facing contract), (3) the cross-cutting 5-tick integration test that proves the prompt is no longer tick-isomorphic. All work in this plan reads `state["user:positions"]` (the persistence key shipped by Plan 1), not bare `state["positions"]`, and assumes `TickerStance.intent` already accepts `hold` and `update` from Plan 1.

**Tech Stack:** Python 3.14, Pydantic v2, Google ADK `BaseAgent`/`LlmAgent`, pytest (incl. `pytest.mark.asyncio` for the integration test), `freezegun` (already used in `tests/backtest/` for clock control). All commands run from project root with `PYTHONPATH=src .venv/bin/python …`.

**Prerequisites:** This plan lands AFTER Plan 1 (memory backbone). At start-of-plan the following invariants must already hold:

- `state["user:positions"]` is populated and persists across ticks via `DatabaseSessionService` (Plan 1 / spec §"Persistence model").
- `state["user:thesis"]` is populated.
- `TickerStance.intent` (or the equivalent stance-verb field) already accepts `hold` and `update` (Plan 1 / spec §"Stance vocabulary").
- Optional per-stance fields `reason`, `target_price`, `stop_price`, `catalyst`, `horizon`, `rationale` exist on `TickerStance` with verb-conditional semantics.
- `PositionThesis` model lives at `src/agents/strategist/position_thesis.py` (Plan 1).
- MemoryWriter writes `user:positions` and `user:thesis` via `state_delta`.
- Executor only writes broker-effect keys (`executions`, `last_executed_tick_id`).

If any of these are not in place, **stop and verify Plan 1 has merged**. None of this plan's tasks are useful against the pre-Plan-1 surface.

---

## File Map

Files this plan creates or modifies. One responsibility per file; tasks below produce self-contained changes.

### Created

| Path | Responsibility |
|---|---|
| `tests/unit/agents/strategist/test_held_view_evolution.py` | Chunk-5 unit tests for the rewritten `held_view.py` — cold-start fallback, evolution columns, Invariant 4 (`last_reviewed_reason` withheld), pct-to-target/stop arithmetic, null target/stop handling |
| `tests/unit/agents/strategist/test_context_shim_mode.py` | Chunk-5 unit tests for the `temp:strategist_mode` injection — cold-start vs incremental selection, N substitution |
| `tests/unit/agents/strategist/test_derivation_stance_required.py` | Chunk-5 unit tests for the post-condition "stance required per held" check (D3) and the symmetric "flat ticker stance optional" case |
| `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py` | Chunk-5 integration test — 5-tick seeded-portfolio backtest against a stub LLM that echoes its prompt back; asserts the Mode header differs on ticks 2-5 vs tick 1 and the Held Positions block is non-empty |

### Modified

| Path | Why |
|---|---|
| `src/agents/strategist/prompts.py` | Chunk 4 — add `COLD_START_MODE_TEMPLATE` and `INCREMENTAL_MODE_TEMPLATE` constants; rewrite `STRATEGIST_INSTRUCTION` to carry a `{temp:strategist_mode}` placeholder and a redesigned Output Requirements block instructing the LLM to emit a stance per held position with a 'what's changed' reason |
| `src/agents/strategist/context_shim.py` | Chunk 4 — read from `state["user:positions"]` instead of bare `state["positions"]`; compute and emit `temp:strategist_mode` based on `len(state["user:positions"])`; pass current `as_of` through to the held-view renderer |
| `src/agents/strategist/held_view.py` | Chunk 4 — rewrite renderer to read from `state["user:positions"]`, accept `as_of: datetime` parameter, render evolution columns (price-vs-entry %, time elapsed "N ticks · Mh · D trading days", distance to target/stop in $ and %, last reviewed at/decision), preserve the flat-portfolio fallback; withhold `last_reviewed_reason` from the rendered output (Invariant 4) |
| `src/agents/strategist/derivation.py` | Chunk 4 — remove the carry-forward block at lines 254-271 (it pads `target_weights` for un-emitted *held* tickers); add a "stance required per held" post-condition that raises `StrategistContractViolation` when a pre-tick held ticker has no matching stance; preserve carry-forward for *flat* watchlist tickers |

---

## Implementation Order

Three bands; tasks numbered top-to-bottom inside this plan.

- **Band 1 — Prompt-surface layer** (Tasks 1–3): held-view rewrite, then `temp:strategist_mode` shim extension, then the prompt-template rewrite. Sequenced so each task's tests can run against a green tree.
- **Band 2 — Derivation contract** (Task 4): carry-forward removal + "stance required per held" post-condition.
- **Band 3 — Cross-cutting integration test** (Task 5): the 5-tick prompt-diversity test that proves the prompt is no longer tick-isomorphic.

Bands 1 and 2 are independent within themselves; Band 3 requires Bands 1 and 2 in place to assert prompt structure.

---

## Band 1 — Prompt-surface layer

### Task 1 — Rewrite `held_view.py` to render evolution columns from `state["user:positions"]`

**Files:**
- Modify: `src/agents/strategist/held_view.py`
- Create: `tests/unit/agents/strategist/test_held_view_evolution.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_held_view_evolution.py`:

```python
"""Chunk 5 — held-view rendering tests for the Spec B rewrite.

The pre-spec ``render_held_positions_view`` rendered an "Opened / Why /
Aim / Horizon / Catalyst / Now" block. The Spec B rewrite splits that
into two blocks per position:

  * ``Your commitments on entry`` — the immutable promise the strategist
    made at open (rationale, target, stop, catalyst, horizon).
  * ``Evolution`` — what has changed since open (price drift, time held,
    distance to target / stop in $ and %, the verb used on the most
    recent review).

This test module covers the new contract end-to-end:
  * empty positions → flat-portfolio fallback unchanged.
  * populated positions → both blocks rendered.
  * Invariant 4 — ``last_reviewed_reason`` MUST NOT appear in the
    rendered text (Principle 2 — the LLM should never read its own
    prior-tick justification).
  * percent-to-target / percent-to-stop arithmetic is computed from the
    CURRENT price (not the entry price), so the LLM sees how much
    further the catalyst has to run.
  * null target / stop renders "no target set" rather than crashing.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.held_view import render_held_positions_view
from agents.strategist.position_thesis import PositionThesis
from broker.portfolio import Portfolio, Position


def _thesis(
    *,
    ticker:                 str = "AVGO",
    opened_at:              datetime = datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
    opened_tick_id:         str = "tick_001",
    opened_price:           float = 100.0,
    weight:                 float = 0.05,
    target_price:           float | None = 120.0,
    stop_price:             float | None =  90.0,
    catalyst:               str | None  = "Q3 guidance call",
    horizon:                str = "swing",
    rationale:              str = "Cloud-AI margin expansion thesis",
    last_reviewed_at:       datetime = datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
    last_reviewed_decision: str = "open",
    last_reviewed_reason:   str = "INVARIANT-4-CANARY: this string must never appear in held-view output",
) -> PositionThesis:
    """Construct a PositionThesis fixture with all fields under test control."""

    return PositionThesis(
        ticker                 = ticker,
        opened_at              = opened_at,
        opened_tick_id         = opened_tick_id,
        opened_price           = opened_price,
        weight                 = weight,
        target_price           = target_price,
        stop_price             = stop_price,
        catalyst               = catalyst,
        horizon                = horizon,
        rationale              = rationale,
        last_reviewed_at       = last_reviewed_at,
        last_reviewed_decision = last_reviewed_decision,
        last_reviewed_reason   = last_reviewed_reason,
    )


def _portfolio(ticker: str = "AVGO", last_price: float = 110.0) -> Portfolio:
    """Single-position portfolio at ``last_price`` so evolution columns can compute."""

    return Portfolio(
        cash      = 950.0,
        positions = {ticker: Position(quantity=1.0, avg_cost=100.0, last_price=last_price)},
    )


def test_held_view_empty_renders_cold_start_fallback() -> None:
    """An empty positions dict must produce the flat-portfolio sentinel."""

    out = render_held_positions_view(
        positions = {},
        portfolio = Portfolio(cash=1000.0, positions={}),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )
    assert out == "(No held positions — portfolio is flat.)"


def test_held_view_renders_evolution_columns() -> None:
    """Populated positions must render both commitments and evolution blocks."""

    thesis = _thesis()
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(last_price=110.0),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )

    # Both block headers must be present.
    assert "Your commitments on entry"   in out
    assert "Evolution"                   in out

    # Evolution columns named in the spec at lines ~588-614 of
    # docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md.
    assert "Held for:"   in out                       # time-elapsed line
    assert "Now:"        in out                       # current price line
    assert "To target:"  in out                       # distance-to-target line
    assert "To stop:"    in out                       # distance-to-stop line
    assert "Reviewed:"   in out                       # last_reviewed line
    assert "(open)"      in out                       # last_reviewed_decision rendered alongside


def test_held_view_does_not_leak_last_reviewed_reason() -> None:
    """Invariant 4 — the rendered text must NOT contain ``last_reviewed_reason``.

    Principle 2 of the spec — the LLM must never read its own prior-tick
    'what's changed' justification. The canary string on the fixture is
    explicitly distinctive so a substring search is sufficient.
    """

    thesis = _thesis()
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )
    assert "INVARIANT-4-CANARY" not in out


def test_held_view_computes_pct_to_target_and_stop_correctly() -> None:
    """Distance-to-target and distance-to-stop are computed from CURRENT price.

    Entry 100, current 110, target 120, stop 90.
    To-target: (120 - 110) / 110 = +9.09 %.
    To-stop:   (90  - 110) / 110 = -18.18 %.
    """

    thesis = _thesis(opened_price=100.0, target_price=120.0, stop_price=90.0)
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(last_price=110.0),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )

    # The exact format string is decided in the implementation step;
    # we assert the rounded percentages survive somewhere in the output.
    assert "+9.1%"  in out  or  "+9.09%"  in out      # to-target
    assert "-18.2%" in out  or  "-18.18%" in out      # to-stop


def test_held_view_handles_null_target_and_stop() -> None:
    """Null target / stop must render "no target set" / "no stop set" — never crash."""

    thesis = _thesis(target_price=None, stop_price=None)
    out = render_held_positions_view(
        positions = {"AVGO": thesis.model_dump(mode="json")},
        portfolio = _portfolio(),
        as_of     = datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
    )

    assert "Your commitments on entry" in out
    # Either render "no target set" / "no stop set" or omit the lines —
    # both are spec-compliant. The non-negotiable contract is no crash
    # and no division-by-None.
    assert "AVGO" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_held_view_evolution.py -v`

Expected: FAIL — every test fails with `TypeError: render_held_positions_view() got an unexpected keyword argument 'as_of'` (the pre-spec signature has no `as_of` parameter). The empty-positions test also fails because the legacy sentinel includes trailing punctuation that the new test pins exactly.

- [ ] **Step 3: Rewrite `held_view.py` to render evolution columns**

Open `src/agents/strategist/held_view.py`. Replace the entire file body (keeping the module docstring updated). The function signature gains the `as_of` parameter and the import switches to the new `PositionThesis` model that Plan 1 ships at `src/agents/strategist/position_thesis.py`:

```python
"""Render the Held Positions block injected into the strategist's prompt.

Reads thesis data from ``state["user:positions"]`` (a ``dict[ticker,
PositionThesis-shaped dict]``) and live price/weight data from
``state["portfolio"]`` (a ``Portfolio`` instance or its serialised
dict equivalent).  Spec B rewrites the renderer to emit two blocks per
position:

  * **Your commitments on entry** — the immutable promise the strategist
    made at open (rationale, target, stop, catalyst, horizon).
  * **Evolution** — what has changed since open (price drift, time
    held, distance to target / stop in $ and %, last-reviewed verb).

``last_reviewed_reason`` is persisted to the audit trail but NEVER
rendered into the next tick's prompt (Principle 2 / Invariant 4) — the
LLM must not anchor on its own prior-tick justification.

The function is *total* — it never raises.  Entries whose thesis cannot
be coerced to ``PositionThesis`` are silently skipped so one corrupt
entry in state does not abort the tick.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.strategist.position_thesis import PositionThesis
from broker.portfolio import Portfolio


# Number of trading hours per day used to convert raw elapsed hours into
# the "D trading days" approximation rendered in the Evolution block.
# NYSE regular hours are 09:30-16:00 = 6.5h; we use 6.5 to keep the
# arithmetic honest on backtests that tick at hourly cadence.
_TRADING_HOURS_PER_DAY: float = 6.5


# ---------------------------------------------------------------------------
# Internal coercion helpers
# ---------------------------------------------------------------------------

def _coerce_thesis(value: Any) -> PositionThesis:
    """Return a ``PositionThesis`` whether ``value`` is an instance or a dict."""

    if isinstance(value, PositionThesis):
        return value
    return PositionThesis.model_validate(value)


def _coerce_portfolio(value: Any) -> Portfolio:
    """Return a ``Portfolio`` whether ``value`` is an instance or a dict."""

    if isinstance(value, Portfolio):
        return value
    return Portfolio.model_validate(value)


# ---------------------------------------------------------------------------
# Evolution arithmetic — small pure helpers so the formatter stays flat
# ---------------------------------------------------------------------------

def _hours_between(earlier: datetime, later: datetime) -> float:
    """Return the elapsed hours between two UTC datetimes (non-negative)."""

    delta = later - earlier
    return max(delta.total_seconds() / 3600.0, 0.0)


def _pct_change(*, from_price: float, to_price: float) -> float | None:
    """Return ``(to - from) / from * 100`` or ``None`` when ``from == 0``."""

    if from_price == 0.0:
        return None
    return (to_price - from_price) / from_price * 100.0


# ---------------------------------------------------------------------------
# Single-position formatter
# ---------------------------------------------------------------------------

def _format_one(
    thesis:    PositionThesis,
    portfolio: Portfolio,
    *,
    as_of:     datetime,
) -> str:
    """Render one position as a two-block (commitments + evolution) string.

    Parameters
    ----------
    thesis:
        The ``PositionThesis`` for this position.
    portfolio:
        Current portfolio snapshot — supplies live price for evolution.
    as_of:
        Current tick timestamp — used to compute "Held for" elapsed time.

    Returns
    -------
    str
        A multi-line block joined by ``\\n``, ready to splice into a prompt.
    """

    ticker        = thesis.ticker
    weights       = portfolio.current_weights()
    curr_weight   = weights.get(ticker, 0.0)
    pos           = portfolio.positions.get(ticker)
    current_price = pos.last_price if pos is not None else None

    # Header line — when the position was opened and at what price.
    opened_str = thesis.opened_at.strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        ticker,
        f"  Opened on {opened_str} at ${thesis.opened_price:.2f}  "
        f"(tick {thesis.opened_tick_id})",
    ]

    # ── Your commitments on entry ────────────────────────────────────────
    # The immutable promise the strategist made when opening the position.
    # Rationale stays visible per Principle 1 (anti-anchoring via framing,
    # not hiding) — we label it "commitments", not "prior conclusion".
    lines.append("  Your commitments on entry:")
    lines.append(f"    Rationale:  {thesis.rationale}")

    if thesis.target_price is not None:
        target_pct = _pct_change(
            from_price = thesis.opened_price,
            to_price   = thesis.target_price,
        )
        pct_str = f"  ({target_pct:+.1f}% from entry)" if target_pct is not None else ""
        lines.append(f"    Target:     ${thesis.target_price:.2f}{pct_str}")
    else:
        lines.append("    Target:     (no target set)")

    if thesis.stop_price is not None:
        stop_pct = _pct_change(
            from_price = thesis.opened_price,
            to_price   = thesis.stop_price,
        )
        pct_str = f"  ({stop_pct:+.1f}% from entry)" if stop_pct is not None else ""
        lines.append(f"    Stop:       ${thesis.stop_price:.2f}{pct_str}")
    else:
        lines.append("    Stop:       (no stop set)")

    lines.append(f"    Catalyst:   {thesis.catalyst or '(none recorded)'}")
    lines.append(f"    Horizon:    {thesis.horizon}")

    # ── Evolution ────────────────────────────────────────────────────────
    # What has changed since open — the structural source of prompt
    # diversity across ticks. Even with a stable held set, these lines
    # mutate as price moves and time advances.
    lines.append("  Evolution:")

    elapsed_hours = _hours_between(thesis.opened_at, as_of)
    elapsed_days  = elapsed_hours / _TRADING_HOURS_PER_DAY
    # "N ticks" — we approximate one tick per hour of trading time. The
    # exact tick count is also available on PositionThesis via
    # opened_tick_id arithmetic in a future revision; for V1 this hour
    # proxy is good enough for the LLM to reason about freshness.
    elapsed_ticks = int(round(elapsed_hours))
    lines.append(
        f"    Held for:   {elapsed_ticks} ticks · "
        f"{elapsed_hours:.1f}h · {elapsed_days:.1f} trading days"
    )

    if current_price is not None and current_price > 0:
        # Now line — current price + signed pct from entry + portfolio weight.
        from_entry = _pct_change(
            from_price = thesis.opened_price,
            to_price   = current_price,
        )
        from_entry_str = f"  ({from_entry:+.1f}% from entry)" if from_entry is not None else ""
        lines.append(
            f"    Now:        ${current_price:.2f}{from_entry_str}  |  "
            f"weight {curr_weight:.3f}"
        )

        # To-target / to-stop — distance from CURRENT price (not entry).
        # Tells the LLM how much further the catalyst still has to run.
        if thesis.target_price is not None:
            delta_target = thesis.target_price - current_price
            pct_target   = _pct_change(from_price=current_price, to_price=thesis.target_price)
            pct_str      = f"  ({pct_target:+.1f}% from now)" if pct_target is not None else ""
            lines.append(f"    To target:  ${delta_target:+.2f}{pct_str}")
        else:
            lines.append("    To target:  (no target set)")

        if thesis.stop_price is not None:
            delta_stop = thesis.stop_price - current_price
            pct_stop   = _pct_change(from_price=current_price, to_price=thesis.stop_price)
            pct_str    = f"  ({pct_stop:+.1f}% from now)" if pct_stop is not None else ""
            lines.append(f"    To stop:    ${delta_stop:+.2f}{pct_str}")
        else:
            lines.append("    To stop:    (no stop set)")
    else:
        # No live price — render placeholders so the LLM still sees the row.
        lines.append("    Now:        (price unavailable)")
        lines.append("    To target:  (price unavailable)")
        lines.append("    To stop:    (price unavailable)")

    # Reviewed line — last-reviewed timestamp + the verb that produced
    # the review. ``last_reviewed_reason`` is DELIBERATELY OMITTED here
    # per Principle 2 / Invariant 4.
    reviewed_str = thesis.last_reviewed_at.strftime("%Y-%m-%d %H:%M")
    lines.append(
        f"    Reviewed:   {reviewed_str} ({thesis.last_reviewed_decision})"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_held_positions_view(
    positions: dict[str, Any],
    portfolio: Any,
    *,
    as_of:     datetime,
) -> str:
    """Render every held position as a structured block for prompt injection.

    Accepts ``positions`` values that are either ``PositionThesis``
    instances or their ``model_dump(mode="json")`` dict equivalents.
    ``portfolio`` may likewise be a ``Portfolio`` instance or its
    serialised dict form.  ``as_of`` is the current tick timestamp used
    to compute the "Held for" evolution column.

    The function is *total* — it never raises.  Entries whose thesis
    cannot be coerced are silently skipped; the remaining entries are
    still rendered.  An entirely empty or unrenderable set of positions
    returns the flat-portfolio sentinel.

    Parameters
    ----------
    positions:
        Mapping of ticker → ``PositionThesis`` (instance or dict).
    portfolio:
        Current portfolio snapshot (``Portfolio`` instance or dict).
    as_of:
        Current tick timestamp.  Required (no default) so the caller is
        forced to thread the replay clock through — wall-clock fallback
        belongs at the call site, not buried here.

    Returns
    -------
    str
        Human-readable block suitable for splicing into an LLM prompt,
        or the flat-portfolio sentinel when there are no valid positions.
    """

    if not positions:
        return "(No held positions — portfolio is flat.)"

    pf = _coerce_portfolio(portfolio)

    blocks: list[str] = []
    for ticker in sorted(positions.keys()):
        try:
            thesis = _coerce_thesis(positions[ticker])
        except Exception:  # noqa: BLE001 — defensive at rendering boundary;
            # one corrupt position dict must not crash the tick.
            continue
        blocks.append(_format_one(thesis, pf, as_of=as_of))

    if not blocks:
        return "(No held positions — portfolio is flat.)"

    # Separate each position block with a blank line for legibility in the prompt.
    return "\n\n".join(blocks)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_held_view_evolution.py -v`

Expected: 5 passed.

- [ ] **Step 5: Run the existing `test_held_view.py` to assess regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_held_view.py -v`

Expected: the existing suite WILL fail because it asserts the legacy "Opened / Why / Aim / Horizon / Catalyst / Now" layout that no longer exists. Update those tests to match the new contract — the legacy assertions are the previous design, not invariants. Concretely, for every failing test:

1. Add the `as_of=datetime(...)` keyword to every `render_held_positions_view(...)` call.
2. Update assertions from `"Opened:"` / `"Why:"` / `"Aim:"` / `"Now:"` to the new block labels: `"Opened on"` / `"Your commitments on entry"` / `"Target:"` / `"Stop:"` / `"Evolution"` / `"Held for:"` / `"To target:"` / `"To stop:"` / `"Reviewed:"`.
3. Update `PositionThesis(...)` constructions to use the new field names from Plan 1's model (`opened_tick_id`, `weight`, `last_reviewed_decision`, `last_reviewed_reason` — replaces `opened_tag` / `last_review_note`).
4. The flat-portfolio sentinel string is now exactly `"(No held positions — portfolio is flat.)"` (no trailing period change; pin via equality, not substring).

If a legacy test is purely asserting against the old layout (e.g. `test_single_holding_block_includes_all_required_lines`), refactor it to assert against the new layout — do not delete; we still want coverage of the rendered shape from both files.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/held_view.py tests/unit/agents/strategist/test_held_view_evolution.py tests/unit/agents/strategist/test_held_view.py
git commit -m "$(cat <<'EOF'
feat(strategist): held-view renders commitments + evolution from user:positions (Spec B Chunk 4)

Rewrites render_held_positions_view to read state["user:positions"]
(populated by Plan 1) and render two blocks per held ticker — the
entry commitments (immutable) and the evolution since open (price
drift, time elapsed, distance to target/stop).  last_reviewed_reason
is persisted but withheld from the prompt per Principle 2 /
Invariant 4.  The "as_of" parameter is now required so the caller
threads the replay clock through to the evolution arithmetic.
EOF
)"
```

---

### Task 2 — Extend `context_shim.py` to compute `temp:strategist_mode` and read `user:positions`

**Files:**
- Modify: `src/agents/strategist/context_shim.py`
- Create: `tests/unit/agents/strategist/test_context_shim_mode.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_context_shim_mode.py`:

```python
"""Chunk 5 — context-shim tests for the temp:strategist_mode emit.

The shim previously emitted exactly three temp keys —
``temp:held_positions_view``, ``temp:ticker_evidence``,
``temp:ticker_evidence_objects``.  Spec B adds a fourth key,
``temp:strategist_mode``, whose value is one of two literal templates:

  * COLD_START_MODE_TEMPLATE  — when ``len(state["user:positions"]) == 0``
  * INCREMENTAL_MODE_TEMPLATE — when there are held positions; the
    ``{N}`` placeholder is substituted with the count.

This module exercises the three contract points called out in the
spec at lines ~694-723: cold-start selection, incremental selection,
and N substitution.  We drive the shim through its public
``_run_async_impl`` so the test exercises the same code path the
runtime pipeline does.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from agents.strategist.context_shim import StrategistContextShim
from agents.strategist.prompts import (
    COLD_START_MODE_TEMPLATE,
    INCREMENTAL_MODE_TEMPLATE,
)
from broker.portfolio import Portfolio


pytestmark = pytest.mark.asyncio


def _fake_ctx(state: dict[str, Any]) -> SimpleNamespace:
    """Build a minimal InvocationContext stand-in carrying ``state``.

    The shim only touches ``ctx.session.state`` and ``ctx.invocation_id``;
    a SimpleNamespace satisfies both attribute reads without dragging in
    the full ADK runtime.
    """

    return SimpleNamespace(
        session       = SimpleNamespace(state=state),
        invocation_id = "test-invocation",
    )


async def _run_shim_and_collect(state: dict[str, Any]) -> dict[str, Any]:
    """Run the shim and return the merged state_delta from its single event."""

    shim = StrategistContextShim()
    merged: dict[str, Any] = {}
    async for event in shim._run_async_impl(_fake_ctx(state)):
        merged.update(event.actions.state_delta or {})
    return merged


async def test_shim_emits_cold_start_mode_when_positions_empty() -> None:
    """``len(state['user:positions']) == 0`` selects the cold-start template."""

    state = {
        "user:positions":          {},
        "portfolio":               Portfolio(cash=1000.0).model_dump(mode="json"),
        "tickers":                 ["AVGO", "MSFT"],
        "tick_id":                 "tick_001",
        "as_of":                   datetime(2026, 5, 1, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    assert delta["temp:strategist_mode"] == COLD_START_MODE_TEMPLATE


async def test_shim_emits_incremental_mode_when_positions_present() -> None:
    """Non-empty ``user:positions`` selects the incremental template."""

    state = {
        "user:positions":          {
            "AVGO": {
                "ticker":                 "AVGO",
                "opened_at":              "2026-05-01T14:00:00+00:00",
                "opened_tick_id":         "tick_001",
                "opened_price":           100.0,
                "weight":                 0.05,
                "target_price":           120.0,
                "stop_price":              90.0,
                "catalyst":               "Q3 guidance",
                "horizon":                "swing",
                "rationale":              "Cloud-AI margin expansion",
                "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
                "last_reviewed_decision": "open",
                "last_reviewed_reason":   "opened on entry signal",
            },
        },
        "portfolio":               Portfolio(cash=950.0).model_dump(mode="json"),
        "tickers":                 ["AVGO"],
        "tick_id":                 "tick_005",
        "as_of":                   datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    # The incremental template carries ``{N}`` — substituted with the count.
    assert delta["temp:strategist_mode"] == INCREMENTAL_MODE_TEMPLATE.format(N=1)


async def test_shim_n_substitution_in_incremental_text() -> None:
    """``{N}`` must reflect the actual count, not a hardcoded value."""

    state = {
        "user:positions":          {
            "AVGO": {
                "ticker":                 "AVGO",
                "opened_at":              "2026-05-01T14:00:00+00:00",
                "opened_tick_id":         "tick_001",
                "opened_price":           100.0,
                "weight":                 0.05,
                "horizon":                "swing",
                "rationale":              "r1",
                "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
                "last_reviewed_decision": "open",
                "last_reviewed_reason":   "x",
            },
            "MSFT": {
                "ticker":                 "MSFT",
                "opened_at":              "2026-05-02T14:00:00+00:00",
                "opened_tick_id":         "tick_002",
                "opened_price":           400.0,
                "weight":                 0.04,
                "horizon":                "swing",
                "rationale":              "r2",
                "last_reviewed_at":       "2026-05-02T14:00:00+00:00",
                "last_reviewed_decision": "open",
                "last_reviewed_reason":   "x",
            },
            "XOM": {
                "ticker":                 "XOM",
                "opened_at":              "2026-05-03T14:00:00+00:00",
                "opened_tick_id":         "tick_003",
                "opened_price":            110.0,
                "weight":                 0.03,
                "horizon":                "swing",
                "rationale":              "r3",
                "last_reviewed_at":       "2026-05-03T14:00:00+00:00",
                "last_reviewed_decision": "open",
                "last_reviewed_reason":   "x",
            },
        },
        "portfolio":               Portfolio(cash=900.0).model_dump(mode="json"),
        "tickers":                 ["AVGO", "MSFT", "XOM"],
        "tick_id":                 "tick_010",
        "as_of":                   datetime(2026, 5, 10, 14, 0, tzinfo=UTC),
        "technical_evidence":      [],
        "fundamental_evidence":    [],
        "news_evidence":           [],
        "smart_money_evidence":    [],
    }

    delta = await _run_shim_and_collect(state)

    # N is the held-position count, not the watchlist length — although
    # here both happen to be 3.  Spec wording at line ~575:
    # "Incremental — you have {N} held positions opened on prior ticks."
    assert "3 held positions" in delta["temp:strategist_mode"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim_mode.py -v`

Expected: FAIL — `ImportError: cannot import name 'COLD_START_MODE_TEMPLATE' from 'agents.strategist.prompts'`. The templates do not exist yet; Task 3 adds them. To unblock Task 2's tests without coupling to Task 3, the implementation below defines the constants on `context_shim.py` for now and we will move them to `prompts.py` in Task 3 (re-exporting from the shim's view for the test).

**Alternative ordering note.** If the implementer prefers strict TDD here, skip ahead to Task 3 Step 3 (add the two constants to `prompts.py`), then return to this task at Step 3. The plan presents the tasks in dependency order so the constants land in `prompts.py` first; the test above already imports from `agents.strategist.prompts`.

- [ ] **Step 3: Define the two mode-template constants in `prompts.py`**

Open `src/agents/strategist/prompts.py`. Immediately after the existing module-level cap resolutions (after the `_CASH_FLOOR_STANZA` block, around line 53), add:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Spec B — Mode header templates
# ─────────────────────────────────────────────────────────────────────────────
# These two literal strings drive the cold-start vs incremental framing
# described in the spec at lines ~562-580.  Selection happens in
# ``StrategistContextShim._run_async_impl``, which substitutes the count and
# emits the chosen template under ``temp:strategist_mode``.  The strategist
# instruction template carries a ``{temp:strategist_mode}`` placeholder which ADK's
# ``inject_session_state`` resolves at runtime.

COLD_START_MODE_TEMPLATE: str = (
    "Cold start — your portfolio is empty.  No prior open positions to evaluate.  "
    "Build an initial portfolio by scanning the watchlist evidence below.  Open "
    "1-3 high-conviction entries.  You may also write or revise the standing "
    "market thesis if you have a view."
)

INCREMENTAL_MODE_TEMPLATE: str = (
    "Incremental — you have {N} held positions opened on prior ticks.  Each is "
    "rendered below with the commitments you made on entry and the evolution "
    "since.  For every held position you MUST emit a stance (hold / trim / "
    "close / update) with a 'what has changed' reason.  You may also scan the "
    "watchlist evidence for fresh entry candidates and open new positions."
)
```

(These constants are deliberately public — exported at module top-level so tests and the shim both import them by name. Keeping them in `prompts.py` colocates all LLM-facing template strings.)

- [ ] **Step 4: Extend `StrategistContextShim._run_async_impl` to emit `temp:strategist_mode`**

Open `src/agents/strategist/context_shim.py`. Three changes:

1. At the top of the file, add the import for the new templates:

```python
from agents.strategist.prompts import (
    COLD_START_MODE_TEMPLATE,
    INCREMENTAL_MODE_TEMPLATE,
)
```

2. Inside `_run_async_impl`, change the `positions = state.get("positions", {}) or {}` line (currently line 117) to read from the user-scoped key. Plan 1 ships `state["user:positions"]`; we keep the fallback to bare `state["positions"]` for the duration of a single tick during the rollout (defence-in-depth — should be empty post-Plan-1 but tests on the migrated code path still benefit from the defence):

```python
        # ── Held-positions view ───────────────────────────────────────────
        # Read from the user-scoped key Plan 1 ships.  The legacy bare
        # ``state["positions"]`` is never written post-Plan-1, but the
        # fallback keeps tests on the migrated code path informative if
        # one slips in.
        positions = state.get("user:positions") or state.get("positions") or {}
```

3. Resolve the mode header text immediately after `positions` is computed, and pass `as_of` through to `render_held_positions_view`. The `recorded_at` resolution block (lines ~128-140) already produces a `datetime` we can re-use:

```python
        portfolio = _coerce_portfolio(state.get("portfolio"))

        # Resolve the ``recorded_at`` / ``as_of`` timestamp for the
        # evolution columns AND the evidence aggregation.  Priority:
        # state["as_of"] (backtest replay clock) > state["recorded_at"]
        # > wall-clock fallback (live, when STOCKBOT_STRICT_AS_OF=0).
        as_of_raw = state.get("as_of")
        if isinstance(as_of_raw, datetime):
            recorded_at = as_of_raw
        else:
            recorded_at_raw = state.get("recorded_at")
            if isinstance(recorded_at_raw, str):
                recorded_at = datetime.fromisoformat(recorded_at_raw)
            elif isinstance(recorded_at_raw, datetime):
                recorded_at = recorded_at_raw
            else:
                recorded_at = resolve_as_of(
                    None, allow_wallclock=True, site="strategist/context_shim",
                )

        held_view = render_held_positions_view(
            positions = positions,
            portfolio = portfolio,
            as_of     = recorded_at,
        )

        # ── Mode header — cold-start vs incremental framing ──────────────
        # Drives the structural diversity of the prompt across ticks.
        # Cold start: portfolio is empty; encourage 1-3 fresh opens.
        # Incremental: emit a stance per held position with a 'what's
        # changed' reason. See Principle 4 in the spec.
        if not positions:
            mode_text = COLD_START_MODE_TEMPLATE
        else:
            mode_text = INCREMENTAL_MODE_TEMPLATE.format(N=len(positions))
```

4. Delete the now-redundant `recorded_at` resolution block that previously sat below the held-view computation. The block above replaces it (it ran *after* held-view; now it runs *before* so we can thread `as_of` through to the renderer).

5. Add `temp:strategist_mode` to the yielded `state_delta`:

```python
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "temp:strategist_mode":         mode_text,
                "temp:held_positions_view":     held_view,
                "temp:ticker_evidence":         ticker_evidence_rendered,
                "temp:ticker_evidence_objects": ticker_evidence_objects,
            }),
        )
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim_mode.py -v`

Expected: 3 passed.

- [ ] **Step 6: Run the existing context-shim tests to assess regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_context_shim.py -v`

Expected: the existing suite may need updates. Two classes of breakage:

- Tests that seed `state["positions"]` (bare key) and expect the shim to render against it — update to seed `state["user:positions"]` instead.
- Tests that snapshot the rendered held-view output — update to match the new "commitments + evolution" layout.

For each failing test, fix the assertion to match the new contract. Do not weaken assertions — the new contract is more discriminating than the old.

- [ ] **Step 7: Commit**

```bash
git add src/agents/strategist/prompts.py src/agents/strategist/context_shim.py tests/unit/agents/strategist/test_context_shim_mode.py tests/unit/agents/strategist/test_context_shim.py
git commit -m "$(cat <<'EOF'
feat(strategist): emit temp:strategist_mode and read user:positions in shim (Spec B Chunk 4)

Adds COLD_START_MODE_TEMPLATE and INCREMENTAL_MODE_TEMPLATE to
prompts.py and extends StrategistContextShim to emit
temp:strategist_mode on a single state_delta alongside the existing
temp:held_positions_view + temp:ticker_evidence keys.  The shim now
reads state["user:positions"] (Plan 1's persisted key) and threads
the as_of replay clock through to the held-view renderer.
EOF
)"
```

---

### Task 3 — Rewrite `STRATEGIST_INSTRUCTION` with `{temp:strategist_mode}` placeholder and redesigned Output Requirements

**Files:**
- Modify: `src/agents/strategist/prompts.py`

- [ ] **Step 1: Inspect the current `STRATEGIST_INSTRUCTION` to identify the exact insert point**

Read `src/agents/strategist/prompts.py` lines 57-149 (the `_RAW_INSTRUCTION` template). The template structure is fine; we add a Mode section above `## Current State` and rewrite the `## Your Job` / `## OUTPUT CONTRACT` blocks to encode the "stance per held position with reason" rule.

The two surgical edits are:

1. Add `## Mode\n{temp:strategist_mode}\n` immediately above `## Current State`.
2. Rewrite the `## Your Job` paragraph from the carry-forward "active stances" wording to the explicit per-held-position requirement.

The OUTPUT CONTRACT table is largely correct — it already lists `HOLD` / `TRIM` / `CLOSE` and so on. The only addition is making the `reason` field's role explicit for the new `hold` / `update` verbs.

- [ ] **Step 2: Apply the prompt-template rewrite**

Open `src/agents/strategist/prompts.py`. Replace the `_RAW_INSTRUCTION` constant body (lines 57-149) with the version below. **Preserve the existing build-time `.replace()` substitution chain (lines 153-166) byte-identical** — only the template literal changes:

```python
_RAW_INSTRUCTION = """
You are the portfolio strategist for an algorithmic trading bot. You decide a
per-ticker stance for the next trading hour.

## Mode
{temp:strategist_mode}

## Current State
Portfolio:    {portfolio}
Memory Buffer (last 8 ticks): {memory_buffer}
Day Digest:   {day_digest}
Thesis:       {thesis}

## Held Positions (your prior decisions, with evolution since open)
{temp:held_positions_view}

## Ticker Evidence (per-analyst breakdown — features, tags, and prose reports)
{temp:ticker_evidence}

## Reading analyst reports
Where an analyst's report contradicts its lean, the lean is the analyst's
final call — treat the report as their reasoning, not their conclusion. You
may still override an analyst, but write down which signal you overweighted
and why.

Treat the digested aggregate as a deterministic input; you may disagree with
it based on context (held position thesis, memory, day digest) — call out
the disagreement in your rationale when you do.

## Your Job

Watchlist for this tick: {tickers}.

**For every held position above**, you MUST emit exactly one stance with
intent ∈ {{hold, trim, close, update}}.  The ``reason`` field on each held
stance must articulate WHAT HAS CHANGED since you opened the position
(price evolution, catalyst status, time elapsed, evidence shift) — even
if your decision is hold.  Silent carry-forward is NOT permitted on held
positions; the validator will reject the response.

**For watchlist tickers you do NOT currently hold**, the active-stances
model applies: emit a stance only for tickers you want to OPEN.  Omitting
a flat ticker carries no implicit commitment.

A "no new opens, all holds" tick is a legitimate response — but every held
position must still have its own stance.

## OUTPUT CONTRACT — every rule is enforced; violations abort the tick

The lifecycle action for each emitted stance is derived from current weight
vs your ``preferred_weight`` (or the explicit ``intent`` verb on the
stance).  The table below is the single source of truth for which fields
must be set per action; the worked examples at the bottom are illustrations,
not a separate ruleset.

| Action / intent | Current → Preferred         | Required fields                                                                              |
|-----------------|-----------------------------|----------------------------------------------------------------------------------------------|
| OPEN            | 0       → > 0               | horizon, target_price, stop_price, rationale (+ optional catalyst)                            |
| ADD             | > 0     → higher (> 0)      | horizon, target_price, stop_price                                                             |
| HOLD            | > 0     → same              | **reason** (what's changed since open)                                                        |
| TRIM            | > 0     → lower (still > 0) | horizon, target_price, stop_price, **trim_reason** (= reason)                                 |
| CLOSE           | > 0     → 0                 | **close_reason** (= reason).  horizon / target_price / stop_price stay null — you are exiting.|
| UPDATE          | > 0     → same              | **reason**, and at least one of target_price / stop_price / catalyst / horizon                |

Schema-level rules (failing these means ADK rejects your response):
- preferred_weight: float in [0.0, 1.0].  Long-only — 0.0 is the floor.

  Hard rules the risk gate enforces after you respond (so a stance that
  violates them will be clamped — propose values that already respect them):
  - Single-ticker weight capped at {{MAX_POSITION_PCT}}%.
  - Per-ticker weight change capped at {{MAX_DELTA_PCT}}% per tick — if you
    want to size up faster, the gate will trim your delta back to
    {{MAX_DELTA_PCT}}% and you ramp over multiple ticks.
  - Total per-tick turnover (sum of |deltas| across watchlist) capped at
    {{MAX_TURNOVER_PCT}}%.
  {{CASH_FLOOR_STANZA}}
- conviction: float in [0.0, 1.0].
- confidence (decision-level): float in [0.0, 1.0].
- horizon: one of "intraday", "swing", "long_term" — or null.
- rationale: ≤{{STANCE_RATIONALE_MAX}} chars.
- catalyst (optional): ≤{{STANCE_CATALYST_MAX}} chars.
- close_reason: ≤{{STANCE_CLOSE_REASON_MAX}} chars.
- trim_reason: ≤{{STANCE_TRIM_REASON_MAX}} chars.
- reasoning (decision-level): ≤{{DECISION_REASONING_MAX}} chars.
- updated_thesis (decision-level): ≤{{DECISION_THESIS_MAX}} chars.
- decision_tag (decision-level): snake_case label, ≤40 chars.
- Off-watchlist tickers are rejected.

## Two worked examples (the rest follow the table above)

OPEN (currently flat, opening at 0.05):
{{"ticker": "XYZ", "preferred_weight": 0.05, "conviction": 0.7,
"rationale": "Strong fundamentals, bullish technical setup",
"horizon": "swing", "target_price": 215.0, "stop_price": 180.0,
"catalyst": "earnings beat expected next week",
"close_reason": null, "trim_reason": null}}

CLOSE (held at 0.05, exiting to 0.0):
{{"ticker": "XYZ", "preferred_weight": 0.0, "conviction": 0.7,
"rationale": "Thesis invalidated by guidance cut",
"horizon": null, "target_price": null, "stop_price": null,
"catalyst": null,
"close_reason": "guidance cut invalidates thesis",
"trim_reason": null}}
"""
```

Two notes on what changed:

- The `## Mode\n{temp:strategist_mode}` block is new (top of the template).
- The `## Your Job` paragraph is rewritten — the old "Tickers you DON'T emit a stance for are read as a carry-forward" sentence is GONE; the new wording requires a stance per held position.
- The OUTPUT CONTRACT table gains a `HOLD` row (already present in the legacy table — kept) and an `UPDATE` row (new). The legacy table already named HOLD; the change is making the `reason` field's role explicit for `HOLD`/`UPDATE`.
- M5 is already complete in the current file (worked examples use `XYZ`, not `AAPL`) — no edit needed in this task.

- [ ] **Step 3: Verify the build-time substitution still produces a valid template**

Run a quick import smoke test to make sure the `.replace()` chain still resolves cleanly:

Run: `PYTHONPATH=src .venv/bin/python -c "from agents.strategist.prompts import STRATEGIST_INSTRUCTION, COLD_START_MODE_TEMPLATE, INCREMENTAL_MODE_TEMPLATE; print('mode placeholder present:', '{temp:strategist_mode}' in STRATEGIST_INSTRUCTION); print('held_view placeholder present:', '{temp:held_positions_view}' in STRATEGIST_INSTRUCTION)"`

Expected: both `True`. The runtime `{...}` placeholders that ADK's `inject_session_state` will substitute (including the new `{temp:strategist_mode}` placeholder) survive the build-time `.replace()` pass; the `{{...}}` markers that get substituted at import time are all `MAX` / percentage tokens.

- [ ] **Step 4: Run the existing strategist-prompt tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_prompts_v2.py tests/unit/test_strategist_prompt_worked_examples_ticker.py tests/unit/test_strategist_prompt_risk_substitutions.py -v`

Expected: tests pass; if any assert on the pre-spec `## Your Job` wording (the carry-forward sentence), update those assertions to match the new wording. Treat the wording change as the new contract — do not roll the wording back.

- [ ] **Step 5: Commit**

```bash
git add src/agents/strategist/prompts.py
git commit -m "$(cat <<'EOF'
feat(strategist): rewrite STRATEGIST_INSTRUCTION with mode placeholder + per-held stance requirement (Spec B Chunk 4)

Adds the {temp:strategist_mode} placeholder above ## Current State and
rewrites ## Your Job to require a stance per held position with a
'what's changed' reason — silent carry-forward is no longer
permitted on held positions.  The OUTPUT CONTRACT table now lists
UPDATE alongside the existing OPEN/ADD/HOLD/TRIM/CLOSE rows; HOLD
requires the new ``reason`` field.  Build-time substitution chain
unchanged.
EOF
)"
```

---

## Band 2 — Derivation contract

### Task 4 — Remove carry-forward for held tickers and add "stance required per held" post-condition

**Files:**
- Modify: `src/agents/strategist/derivation.py`
- Modify: `src/agents/strategist/agent.py` (the after-callback that invokes derivation)
- Create: `tests/unit/agents/strategist/test_derivation_stance_required.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/agents/strategist/test_derivation_stance_required.py`:

```python
"""Chunk 5 — D3 derivation tests for the held-stance post-condition.

Spec B removes the carry-forward block at ``derivation.py:254-271``
(which padded ``target_weights`` for un-emitted *held* tickers) and
replaces it with an explicit post-condition: every pre-tick held
ticker MUST have a matching stance in the strategist's output.

Carry-forward for *flat* watchlist tickers (the active-stances model)
stays in place — flat tickers carry no implicit commitment, so omitting
them remains legal.

This module pins the two halves of the new contract:
  * D3-violation case — a held ticker with no stance raises
    ``StrategistContractViolation``.
  * D3-compliant case — a flat ticker with no stance is OK.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.risk_gate.lifecycle import StrategistContractViolation
from agents.strategist.derivation import (
    TickContext,
    derive_legacy_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(
    *,
    current_weights: dict[str, float],
    watchlist:       list[str],
) -> TickContext:
    """Build a TickContext fixture with sensible defaults."""

    return TickContext(
        tick_id          = "tick_005",
        decision_tag     = "afternoon_sweep",
        now              = datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
        current_weights  = current_weights,
        watchlist        = watchlist,
    )


def test_held_ticker_without_stance_raises_validation_error() -> None:
    """A pre-tick held ticker with no matching stance must raise.

    AVGO is held at 0.05; the strategist emits a stance only for MSFT
    (a flat watchlist ticker).  Derivation must refuse — silent
    carry-forward is no longer permitted on held positions.
    """

    stances = [
        TickerStance(
            # ``intent`` is required on TickerStance post Plan 1 Task 9 —
            # MSFT is flat with a fresh bullish stance, so "open" is the
            # honest verb here.
            intent           = "open",
            ticker           = "MSFT",
            preferred_weight = 0.03,
            conviction       = 0.7,
            rationale        = "Open on bullish technical setup",
            horizon          = "swing",
            target_price     = 450.0,
            stop_price       = 380.0,
        ),
    ]

    with pytest.raises(StrategistContractViolation) as excinfo:
        derive_legacy_fields(
            stances,
            _ctx(
                current_weights = {"AVGO": 0.05},
                watchlist       = ["AVGO", "MSFT", "XOM"],
            ),
        )

    # The error message should name the violated ticker so the LLM-facing
    # log is debuggable.
    assert "AVGO" in str(excinfo.value)


def test_flat_ticker_without_stance_is_ok() -> None:
    """Omitting a flat watchlist ticker is the active-stances model — legal.

    AVGO is held and has a stance; MSFT and XOM are flat and have no
    stance.  Derivation must succeed and pad target_weights for the
    flat tickers with their current weight (0.0).
    """

    stances = [
        TickerStance(
            # ``intent`` is required on TickerStance post Plan 1 Task 9 —
            # AVGO is held and the rationale articulates a review without
            # weight change, so "hold" is the honest verb.  Task 9's
            # verb-conditional validator requires ``reason`` on hold.
            intent           = "hold",
            ticker           = "AVGO",
            preferred_weight = 0.05,
            conviction       = 0.7,
            rationale        = "Hold — thesis intact, evidence steady",
            reason           = "No new evidence; commitments unchanged.",
            horizon          = "swing",
            target_price     = 120.0,
            stop_price       =  90.0,
        ),
    ]

    derived = derive_legacy_fields(
        stances,
        _ctx(
            current_weights = {"AVGO": 0.05},
            watchlist       = ["AVGO", "MSFT", "XOM"],
        ),
    )

    # Held ticker — its emitted weight is preserved.
    assert derived.target_weights["AVGO"] == 0.05
    # Flat tickers — carry-forward pads to 0.0 (their current weight).
    assert derived.target_weights["MSFT"] == 0.0
    assert derived.target_weights["XOM"]  == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_derivation_stance_required.py -v`

Expected:
- `test_held_ticker_without_stance_raises_validation_error` FAILS with `DID NOT RAISE` — the pre-spec derivation silently carries AVGO forward.
- `test_flat_ticker_without_stance_is_ok` PASSES on the existing carry-forward logic.

- [ ] **Step 3: Add the "stance required per held" post-condition to `derivation.py`**

Open `src/agents/strategist/derivation.py`. Two surgical edits:

1. **No new import needed.** Plan 1 Task 8b deletes `agents.risk_gate.lifecycle` and relocates `StrategistContractViolation` to `derivation.py` itself, so the exception class is already in this module's namespace by the time you reach this task. (If Plan 1 Task 8b has not yet landed when you start this task, you will need to import from the old path temporarily and switch the import after Task 8b merges — coord note 6 tracks this.)

2. Inside `derive_legacy_fields`, **before** Pass 2 (the carry-forward padding loop at lines ~254-271), add a Pass 1.5 that checks every held ticker is covered. **Then** narrow Pass 2 so it ONLY carries forward FLAT tickers — held tickers must have been covered by Pass 1 already.

Note: Plan 1 Task 8b has already removed the `PositionThesis(...)` constructor from the `if action == "open":` branch and dropped `new_positions` from `DerivedFields`, so Pass 1 below has no `open` arm — `target_weights` and `decision_tags` are written unconditionally for every emitted stance, and only `close` / `trim` carry side-effects beyond that.

Replace the body from `# ── Pass 1: emitted stances ──` through the end of the carry-forward loop with:

```python
    # ── Pass 1: emitted stances ───────────────────────────────────────────────
    # Whatever the strategist explicitly said about a ticker takes precedence
    # over the carry-forward default applied in Pass 2 below.
    emitted: set[str] = set()
    for stance in stances:

        emitted.add(stance.ticker)

        # Every stance contributes its preferred weight regardless of action.
        target_weights[stance.ticker] = stance.preferred_weight

        # Determine what needs to happen based on current vs preferred weight.
        current = ctx.current_weights.get(stance.ticker, 0.0)
        action = derive_lifecycle_action(current, stance.preferred_weight)

        # S6: derive a per-ticker intent tag from the (prior, new) weight pair.
        decision_tags[stance.ticker] = derive_decision_tag(
            prior = current,
            new   = stance.preferred_weight,
        )

        # NB: no `if action == "open":` arm — Plan 1 Task 8b removed the
        # PositionThesis construction here.  MemoryWriter assembles
        # user:positions from stances + executions[].fill_price (Plan 1
        # Task 12), which is the only place an honest opened_price is
        # available.
        if action == "close" and stance.close_reason:
            close_reasons[stance.ticker] = stance.close_reason

        elif action == "trim" and stance.trim_reason:
            trim_reasons[stance.ticker] = stance.trim_reason

        # "open", "add" and "hold" actions: target_weights + decision_tags
        # already set above; nothing else to do here.

    # ── Pass 1.5: stance required per held position (Spec B / D3) ────────────
    # Every pre-tick held ticker MUST have been touched by a stance above.
    # Silent carry-forward of held positions is no longer permitted — the
    # strategist must explicitly engage with each held position on every
    # tick (Principle 3 of the spec).  Flat tickers remain optional (the
    # active-stances model survives for them — Pass 2 below).
    held_tickers = {
        t for t, w in ctx.current_weights.items() if w > 0.0
    }
    uncovered_held = held_tickers - emitted
    if uncovered_held:
        # Sort for deterministic error messages — easier to grep for in logs.
        names = ", ".join(sorted(uncovered_held))
        raise StrategistContractViolation(
            f"Held position(s) {{{names}}} have no matching stance in the "
            f"strategist's output.  Every pre-tick held ticker must be "
            f"explicitly engaged with on every tick (Spec B / D3) — emit a "
            f"hold / trim / close / update stance for each."
        )

    # ── Pass 2: carry-forward padding for FLAT tickers only ──────────────────
    # Any *flat* watchlist ticker the strategist did not emit a stance for
    # keeps its current weight (0.0) — the active-stances model survives for
    # flat tickers since the LLM has no view to commit to.  Held tickers are
    # NOT padded here — Pass 1.5 above guarantees they were covered by an
    # explicit stance.
    for ticker in ctx.watchlist:
        if ticker in emitted:
            continue
        # By construction (Pass 1.5), ticker is NOT in held_tickers — so its
        # current weight is 0.0 (or absent) and we pad with 0.0.
        target_weights[ticker] = 0.0
        decision_tags[ticker]  = derive_decision_tag(prior=0.0, new=0.0)
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_derivation_stance_required.py -v`

Expected: 2 passed.

- [ ] **Step 5: Run the existing derivation + strategist after-callback tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/test_derivation.py tests/unit/agents/strategist/test_strategist_callbacks_v2.py -v`

Expected: some legacy tests will fail because they relied on silent carry-forward of held tickers. For each failure, update the test to emit a stance for the held ticker (matching the new contract). Tests asserting the active-stances model for FLAT tickers should still pass — only the held-ticker omission cases break.

If a test was specifically asserting that held carry-forward worked (e.g. `test_carry_forward_pads_held_tickers_at_current_weight`), invert it: the new contract is that this raises `StrategistContractViolation`. Rename the test to reflect the new invariant.

- [ ] **Step 6: Commit**

```bash
git add src/agents/strategist/derivation.py tests/unit/agents/strategist/test_derivation_stance_required.py tests/unit/agents/strategist/test_derivation.py tests/unit/agents/strategist/test_strategist_callbacks_v2.py
git commit -m "$(cat <<'EOF'
feat(strategist): require a stance per held position; remove held carry-forward (Spec B Chunk 4 / D3)

Replaces derivation.py:254-271's carry-forward block with an explicit
post-condition — every pre-tick held ticker must be touched by a
stance in the strategist's output.  Uncovered held tickers raise
StrategistContractViolation (the existing abort path the after-
callback already wires).  The active-stances model survives for flat
watchlist tickers; they remain optional.
EOF
)"
```

---

## Band 3 — Cross-cutting integration test

### Task 5 — 5-tick backtest with stub LLM asserts prompt diversity across ticks

**Files:**
- Create: `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`

- [ ] **Step 1: Inspect the existing integration scaffolding**

Read these files end-to-end before writing the test — they show the established patterns for stubbing LLMs and driving a multi-tick run:

- `tests/integration/test_strategist_v2_smoke.py` — strategist-only smoke test with a stubbed LLM.
- `tests/integration/backtest/test_driver_one_tick.py` — one-tick driver invocation.
- `tests/integration/conftest.py` — shared fixtures (FakeBroker, in-memory session service, etc.).

Use whichever scaffolding the existing suite exposes. The key requirements for this test are:

1. A stub LLM whose response is deterministic and echoes the prompt back into a recordable location (e.g. captures the rendered prompt onto a list).
2. A seeded `state["user:positions"]` so tick 1 vs tick 2 prompts differ on the held set.
3. A way to run 5 sequential ticks through the strategist agent (or the full pipeline — strategist-only is sufficient for prompt diversity).

- [ ] **Step 2: Write the integration test**

Create `tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`:

```python
"""Chunk 5 — multi-tick prompt-diversity integration test.

The "stuck on tick 1" pathology that motivated Spec B is the
strategist producing byte-identical rationale across all sampled
ticks of the baseline-2025-09 / first-test run.  The root cause was
prompt isomorphism — same evidence, same instruction text, same
output by design.

Spec B's Chunks 4-5 fix that by making the prompt structurally
different across ticks:
  * Tick 1 (cold start) — Mode header reads "Cold start — your
    portfolio is empty"; Held Positions block is the flat-portfolio
    sentinel.
  * Tick N > 1 (incremental) — Mode header reads "Incremental —
    you have N held positions opened on prior ticks"; Held Positions
    block renders the evolution columns.

This integration test runs a 5-tick backtest against a stub LLM that
echoes its prompt back (so we can inspect every prompt that was sent)
with a portfolio that is empty on tick 1 and seeded with one
position from tick 2 onwards.  It asserts:

  (a) The Mode header text differs on ticks 2-5 vs tick 1 (cold-start
      vs incremental framing).
  (b) The Held Positions block is non-empty on ticks 2-5 — at minimum
      it contains the seeded ticker symbol and the "Evolution" label.

Together these prove the prompt is no longer tick-isomorphic; an LLM
running against this surface cannot produce byte-identical rationale
because the input itself differs.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from agents.strategist.context_shim import StrategistContextShim
from agents.strategist.prompts import (
    COLD_START_MODE_TEMPLATE,
    INCREMENTAL_MODE_TEMPLATE,
    STRATEGIST_INSTRUCTION,
)
from broker.portfolio import Portfolio


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Stub LLM — captures the rendered prompt onto a list for later inspection
# ---------------------------------------------------------------------------

class _PromptRecorder:
    """Receives the rendered strategist prompt at each tick.

    The shim resolves the {temp:strategist_mode} and {temp:held_positions_view}
    placeholders by emitting them as state_delta keys; the LlmAgent's
    ``inject_session_state`` then does the final ``.format(**state)`` pass
    before the request is sent.  We short-circuit the LLM call by
    capturing the post-injection prompt directly here.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def capture(self, instruction: str, state: dict[str, Any]) -> None:
        """Resolve runtime placeholders and append the resulting prompt."""

        # Only resolve the placeholders the test cares about — the full
        # ADK inject_session_state pass also resolves {portfolio} etc.
        # which we do not need for the diversity assertion.
        resolved = (
            instruction
            .replace("{temp:strategist_mode}",          state.get("temp:strategist_mode", ""))
            .replace("{temp:held_positions_view}", state.get("temp:held_positions_view", ""))
        )
        self.prompts.append(resolved)


# ---------------------------------------------------------------------------
# Pipeline driver — runs the shim only (the LLM call is stubbed)
# ---------------------------------------------------------------------------

async def _run_one_tick(
    *,
    state:    dict[str, Any],
    recorder: _PromptRecorder,
) -> None:
    """Run StrategistContextShim once and capture the resolved prompt.

    We invoke the shim's ``_run_async_impl`` directly with a fake context,
    merge its state_delta into ``state`` (mimicking ADK's session merge),
    and then ask the recorder to resolve the instruction template against
    the post-shim state.  This is sufficient to assert prompt diversity
    without spinning up the full pipeline.
    """

    from types import SimpleNamespace

    ctx = SimpleNamespace(
        session       = SimpleNamespace(state=state),
        invocation_id = f"tick-{len(recorder.prompts) + 1}",
    )

    shim = StrategistContextShim()
    async for event in shim._run_async_impl(ctx):
        state.update(event.actions.state_delta or {})

    recorder.capture(STRATEGIST_INSTRUCTION, state)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

async def test_multi_tick_backtest_produces_diverse_rationale() -> None:
    """Tick 1 prompt differs structurally from ticks 2-5 prompts."""

    # ── Fixture state ────────────────────────────────────────────────────
    # Tick 1: portfolio empty → cold-start mode, flat-portfolio held-view.
    # Tick 2-5: one seeded position → incremental mode, populated held-view.
    seeded_position = {
        "ticker":                 "AVGO",
        "opened_at":              "2026-05-01T14:00:00+00:00",
        "opened_tick_id":         "tick_001",
        "opened_price":           100.0,
        "weight":                 0.05,
        "target_price":           120.0,
        "stop_price":              90.0,
        "catalyst":               "Q3 guidance call",
        "horizon":                "swing",
        "rationale":              "Cloud-AI margin expansion thesis",
        "last_reviewed_at":       "2026-05-01T14:00:00+00:00",
        "last_reviewed_decision": "open",
        "last_reviewed_reason":   "opened on entry signal",
    }

    portfolio = Portfolio(cash=950.0).model_dump(mode="json")
    recorder  = _PromptRecorder()

    base_state = {
        "portfolio":            portfolio,
        "tickers":              ["AVGO", "MSFT"],
        "technical_evidence":   [],
        "fundamental_evidence": [],
        "news_evidence":        [],
        "smart_money_evidence": [],
    }

    # Run 5 ticks at hourly cadence.
    as_of_start = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
    for i in range(5):
        # Tick 1 — empty positions; ticks 2-5 — seeded.
        positions = {} if i == 0 else {"AVGO": seeded_position}

        state = {
            **base_state,
            "user:positions": positions,
            "tick_id":        f"tick_{i + 1:03d}",
            "as_of":          as_of_start + timedelta(hours=i),
        }
        await _run_one_tick(state=state, recorder=recorder)

    assert len(recorder.prompts) == 5

    tick1 = recorder.prompts[0]
    ticks_n = recorder.prompts[1:]

    # ── Assertion 1 — Mode header text differs on ticks 2-5 vs tick 1 ────
    # Tick 1: cold-start template substring present.
    assert COLD_START_MODE_TEMPLATE in tick1, (
        "Tick 1 prompt is missing the cold-start mode header"
    )

    for i, prompt in enumerate(ticks_n, start=2):

        # Each subsequent tick: incremental template substring present
        # with N=1 substituted.
        expected = INCREMENTAL_MODE_TEMPLATE.format(N=1)
        assert expected in prompt, (
            f"Tick {i} prompt is missing the incremental mode header "
            f"(expected substring not found)"
        )

        # And the cold-start template MUST NOT also be present — the two
        # modes are mutually exclusive at the substring level.
        assert COLD_START_MODE_TEMPLATE not in prompt, (
            f"Tick {i} prompt contains both the cold-start AND "
            f"incremental templates — modes leaked across each other"
        )

    # ── Assertion 2 — Held Positions block is non-empty on ticks 2-5 ─────
    # Tick 1: flat-portfolio sentinel present.
    assert "(No held positions — portfolio is flat.)" in tick1

    for i, prompt in enumerate(ticks_n, start=2):

        # The seeded ticker symbol must appear in the rendered held-view.
        assert "AVGO" in prompt, (
            f"Tick {i} prompt does not render the seeded AVGO position"
        )

        # The Evolution block header — proves the rewritten renderer
        # was actually invoked, not a stale legacy renderer.
        assert "Evolution" in prompt, (
            f"Tick {i} prompt is missing the Evolution block header"
        )

        # And the flat-portfolio sentinel MUST NOT be present alongside
        # a populated held set.
        assert "(No held positions — portfolio is flat.)" not in prompt, (
            f"Tick {i} prompt contains the flat-portfolio sentinel "
            f"despite a seeded held set"
        )

    # ── Assertion 3 — prompts are not byte-identical across ticks 2-5 ────
    # The evolution columns mutate over time (Held for: increments, To
    # target/stop deltas if price moved).  Here price is fixed by the
    # fixture so only "Held for" mutates — but that is enough to defeat
    # byte-identical prompts.  This is the actual "stuck on tick 1"
    # pathology Spec B closes.
    unique_prompts = {p for p in ticks_n}
    assert len(unique_prompts) == len(ticks_n), (
        f"Ticks 2-5 produced only {len(unique_prompts)} unique prompts; "
        f"expected {len(ticks_n)} — the Held-for evolution column "
        f"should advance every tick"
    )
```

- [ ] **Step 3: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py -v`

Expected: 1 passed.

- [ ] **Step 4: Run the wider test suite to confirm no regression across the plan**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/strategist/ tests/integration/ -v`

Expected: every test passes (or has been explicitly updated by Tasks 1-4 to match the new contract). Any unrelated failures must be triaged before commit — the multi-tick diversity test is the last load-bearing assertion in this plan, so green-tree here is the completion criterion.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py
git commit -m "$(cat <<'EOF'
test(integration): 5-tick prompt-diversity test proves Spec B closes the byte-identical pathology

Runs the strategist context shim 5 times — tick 1 with empty
positions, ticks 2-5 with one seeded position — and asserts the
resolved prompt's Mode header differs across the cold-start /
incremental boundary, the Held Positions block is non-empty on
ticks 2-5, and the prompts are not byte-identical across ticks 2-5
(the Held-for evolution column mutates every tick).  This is the
direct invariant for the "stuck on tick 1" pathology described in
analysis_computational.md §3.4.
EOF
)"
```

---

## Self-Review Checklist

After implementation completes, run the following to confirm spec coverage:

- [ ] **Held-view fields rendered.** `held_view.py` renders price-vs-entry %, time elapsed "N ticks · Mh · D trading days", distance-to-target/stop in $ and %, last-reviewed at + decision (Task 1).
- [ ] **Invariant 4 holds.** `last_reviewed_reason` does not appear in the rendered held-view (Task 1, `test_held_view_does_not_leak_last_reviewed_reason`).
- [ ] **Mode header injected.** `temp:strategist_mode` is emitted by the context shim, cold-start when empty / incremental with N substituted otherwise (Task 2).
- [ ] **State key migration.** The shim and held-view read `state["user:positions"]`, not bare `state["positions"]` (Tasks 1, 2).
- [ ] **Prompt template carries `{temp:strategist_mode}`.** `STRATEGIST_INSTRUCTION` contains the placeholder; the cold-start and incremental constants live in `prompts.py` (Task 3).
- [ ] **Output Requirements rewritten.** The `## Your Job` block requires a stance per held position with a 'what's changed' reason; carry-forward wording removed (Task 3).
- [ ] **Carry-forward block removed.** `derivation.py` no longer pads held tickers at lines 254-271; Pass 2 only carries forward flat tickers (Task 4).
- [ ] **D3 post-condition raises.** A held ticker with no matching stance raises `StrategistContractViolation` (Task 4, `test_held_ticker_without_stance_raises_validation_error`).
- [ ] **Flat carry-forward survives.** A flat watchlist ticker with no stance is still legal (Task 4, `test_flat_ticker_without_stance_is_ok`).
- [ ] **Prompt diversity end-to-end.** The 5-tick integration test asserts cold-start vs incremental framing and non-byte-identical prompts on ticks 2-5 (Task 5).

---

## Open coordination notes (for the Plan 1 author)

The following seams arose during plan-writing — flag for cross-checking against Plan 1 before either plan merges.

1. **`PositionThesis` field shape — Plan 1 owns the model definition.** This plan's `held_view.py` rewrite reads the following field names (from spec §"Schema — `PositionThesis`"):

   - `ticker`, `opened_at`, `opened_tick_id`, `opened_price`, `weight`
   - `target_price`, `stop_price`, `catalyst`, `horizon`, `rationale`
   - `last_reviewed_at`, `last_reviewed_decision`, `last_reviewed_reason`

   The legacy `PositionThesis` at `src/agents/strategist/schema.py` uses different field names (`opened_tag`, `last_review_note` — no `weight`, no `last_reviewed_decision`, no `last_reviewed_reason`). Plan 1 must ship the new model at `src/agents/strategist/position_thesis.py` with **exactly** the field names listed above. If Plan 1 names anything differently, Task 1 of this plan needs a rename pass before merging.

   **Resolved (2026-05-23):** Plan 1 Task 8b removes the `PositionThesis(...)` constructor from `derive_legacy_fields` and drops the `new_positions` field from both `DerivedFields` and `StrategistDecision`. MemoryWriter (Plan 1 Task 12) assembles `user:positions` directly from stances + `executions[].fill_price`. Plan 2's Task 4 Step 3 has been updated to omit the constructor block from the replacement code.

2. **`TickerStance.intent` field — Plan 1 introduces it.** Task 4's derivation patch does NOT currently switch from weight-based action inference (`derive_lifecycle_action(current, preferred_weight)`) to intent-verb dispatch. Plan 1 is expected to add `intent: Literal["open", "add", "trim", "close", "hold", "update"]` to `TickerStance` and update the executor / memory_writer to use it. If Plan 1 also changes the derivation path, coordinate the merge order so this plan's `Pass 1.5` check still runs against the correct surface. Concretely, the held-stance post-condition only needs the set of `stance.ticker` values — it is agnostic to whether the action is inferred from weight or from `intent`. No change required in Task 4 either way.

3. **`StrategistContractViolation` is an abort, not a retry.** The spec at lines ~1162-1166 says "Rejections raise a retryable validation error which the existing `src/agents/llm_retry.py` layer feeds back to the LLM". Inspection shows `llm_retry.py` only retries on resource-exhausted exceptions; `StrategistContractViolation` is an immediate abort raised by the after-callback. Plan 2 follows the existing abort pattern — the test in Task 4 asserts `pytest.raises(StrategistContractViolation)`. If the spec author wants true LLM-feedback retry, that is a separate piece of work (extend `llm_retry.py` to classify `StrategistContractViolation` as retryable and feed the violation message back as a re-prompt). Worth confirming whether Plan 1's contract amendments touch this.

4. **`state["user:positions"]` shape.** This plan assumes the dict value at each ticker is a `PositionThesis.model_dump(mode="json")` payload (i.e. a flat JSON-compatible dict with the field names listed in note 1). If Plan 1's MemoryWriter chooses a different serialisation (e.g. nests under a `thesis` key), Task 1's `_coerce_thesis` will fail and the integration test in Task 5 needs the fixture shape updated. Lock in the wire shape with Plan 1 before merging.

5. **`temp:strategist_mode` placeholder name.** Plan 2 uses the prefixed `{temp:strategist_mode}` form for the prompt placeholder, matching the existing `{temp:held_positions_view}` convention in `prompts.py`. The temp state key is `temp:strategist_mode`. No further coordination required — both forms resolve through ADK's `inject_session_state` identically; consistency was the deciding factor.

6. **`StrategistContractViolation` import path moves.** Plan 1 Task 8b deletes `src/agents/risk_gate/lifecycle.py` (the function `validate_lifecycle_contract` was only called from tests; Task 9's verb-conditional `model_validator` on `TickerStance` enforces the same invariant at schema-parse time). `StrategistContractViolation` relocates to `src/agents/strategist/derivation.py`, where Plan 2 Task 4's Pass 1.5 is its only remaining caller. Plan 2 Task 4 Step 1 still imports `from agents.risk_gate.lifecycle import StrategistContractViolation` — that import must change to `from agents.strategist.derivation import StrategistContractViolation`. **Merge-order rule:** Plan 1 Task 8b must merge before Plan 2 Task 4; the test file in Plan 2 Task 4 Step 1 should use the new import path from the start. If Plan 2 Task 4 lands first, the test will break at collection time with `ImportError` on `agents.risk_gate.lifecycle`.

7. **`TickerStance.intent` is required (no default).** Plan 1 Task 9 makes `intent: Literal["open","add","trim","close","hold","update"]` a required field. Plan 2 Task 4 test fixtures supply `intent` explicitly (Step 1 — MSFT uses `intent="open"`, AVGO uses `intent="hold"` with a `reason`). Plan 1's verb-conditional validator requires `reason` on `hold` / `update` stances, so the AVGO fixture passes one. If Plan 1 changes the verb-conditional validation surface, audit the two fixtures in Task 4 Step 1.

---

## Open questions (unresolved from the spec alone)

1. **Trading-hours-per-day constant in `held_view.py`.** Task 1 hardcodes `_TRADING_HOURS_PER_DAY = 6.5` for the "D trading days" approximation in the Evolution block. The spec doesn't pin this number. Options: (a) leave hardcoded (current plan), (b) move to `config/strategist.json`, (c) compute exact trading-day count via `pandas_market_calendars`. (a) is the YAGNI choice; flag for review.
2. **`elapsed_ticks` proxy.** Task 1 approximates "N ticks" as `int(round(elapsed_hours))` — this assumes a one-tick-per-hour cadence (matching the existing NYSE hourly schedule in `src/backtest/schedule.py`). If the live cadence ever shifts (sub-hourly, multi-hour), this proxy becomes wrong. Spec leaves this open at line ~601. Bias: leave the proxy; revisit when cadence changes.
3. **`reason` field on `TickerStance` — schema enforcement of "what's changed" wording.** The new prompt requires `reason` on every held stance, but the schema cannot tell whether a string actually articulates "what's changed". The spec accepts this trade-off (validation is field-presence only — lines 1148-1152). No action needed; flag for completeness.
4. **Integration test scope.** Task 5 runs the shim only, not the full pipeline. The spec's listed test (`tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py`) could plausibly run the whole strategist pipeline including the LlmAgent's `inject_session_state` pass. The shim-only scope is sufficient to assert prompt diversity (the load-bearing claim); a full-pipeline variant could be added in a follow-on. Bias: ship shim-only first; expand only if prompt diversity ever regresses despite this test being green.
