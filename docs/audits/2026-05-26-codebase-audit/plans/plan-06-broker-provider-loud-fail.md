# Plan 06 — Broker + Data-Provider Loud-Fail Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate every silent-degradation path in `src/broker/trading212.py` and the data-provider boundary so that absent, unknown, malformed, or out-of-range responses raise loudly instead of returning `[]`, `None`, or sentinel zeros.

**Architecture:** Tactical fixes at module boundaries. No new abstractions. Every change either (a) removes a `try/except` swallow site, (b) replaces `return []` with `raise`, or (c) replaces a sentinel zero with a raised error and a re-pointed test. The fallback provider "shells" (memory: every domain keeps its registered providers) stay registered — they just stop emitting synthetic empties.

**Tech Stack:** Python 3.12, `httpx` (sync `Response.json`), `pytest` + `pytest-asyncio`, `unittest.mock` (`MagicMock` for sync attrs, `AsyncMock` only for coroutines), existing `data.secrets.SecretMissingError`, existing `broker.protocol.BrokerRejection`.

**Audit findings covered (cross-referenced against `docs/audits/2026-05-26-codebase-audit/FINDINGS.md`):**

| ID    | Severity | Summary                                                                 |
| ----- | -------- | ----------------------------------------------------------------------- |
| A-003 | P0       | Trading212 `await resp.json()` runtime bug                              |
| A-004 | P0       | Trading212 `get_portfolio` silently drops unknown instruments           |
| A-006 | P0       | Snapshotter SPY-fetch silently substitutes `spy_price=0.0`              |
| A-007 | P0       | Finnhub social-sentiment soft-fails to empty on every API exception     |
| A-031 | P1       | Snapshotter integration test patches `yfinance.Ticker` (wrong target)   |
| A-039 | P1       | News providers return `[]` on reversed window                           |
| A-040 | P1       | Providers return `[]` on missing API key                                |

**Trust contract:**

- **This plan trusts:** Plan 01 (safe deletions) only. Disjoint from Plans 02–05; may be implemented in parallel with them.
- **Later plans trust this plan to land:** every data-provider boundary raises loudly on absent/unknown/malformed responses. Plan 10 (backtest hygiene) and Plan 11 (test consolidation) assume no provider silently yields `[]` or `null` to mask failure.
- **Out of scope** (covered elsewhere): A-005 FakeBroker `_prices` leak (Plan 05 risk_gate/executor handoff); A-008 executor callback `print`-swallow (Plan 05 risk_gate/executor handoff); A-037 deletion of `news.alpha_vantage` module (Plan 08 provider cull) — but A-039's reversed-window fix is applied to it in-place here unless Plan 08 has already deleted it (see Task 6 Step 4 conditional). **A-041 (`set_active_provider` guard) is owned by Plan 08 — the duplicate task has been removed from this plan.**

---

## Boundary inventory

| Entrypoint                                                              | Today                                                                              | After this plan                                                                                                                            |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `broker.trading212.Trading212Broker.submit_market`                      | `await resp.json()` (sync method, `TypeError` in real httpx; test uses `AsyncMock`) | `resp.json()` (no await); test rewritten with `MagicMock` for `.json`                                                                      |
| `broker.trading212.Trading212Broker.position_size`                      | Same await bug                                                                     | Same fix                                                                                                                                   |
| `broker.trading212.Trading212Broker.get_portfolio`                      | Same await bug; silently `continue`s over instruments missing from `rev` map        | Drop await; `raise BrokerRejection` listing unknown T212 codes (no silent drop)                                                            |
| `agents.snapshot.agent` SPY-fetch                                       | `except Exception: spy_price = 0.0` no log; first-tick anchor at 0.0 permanently breaks every return calc | `logger.exception(...)` and re-raise on first tick (anchor); on subsequent ticks, log loud WARNING and re-use prior `spy_price` anchor; never silently anchor at 0.0 |
| `tests/integration/test_snapshotter.py::test_snapshotter_writes_state` | Patches `yfinance.Ticker` (no-op — masks A-006)                                    | Patches `data.get_price_history`; asserts `snap["spy_price"] > 0`                                                                          |
| `data.providers.social_sentiment.finnhub.fetch`                         | Returns empty `SocialSentiment` on every `FinnhubAPIException`                     | Raises on 4xx (except a documented 403 "premium-gate" path that raises a typed `PremiumGatedError`)                                        |
| `data.providers.news.finnhub.fetch`                                     | `return []` when reversed window after PIT clip                                    | `raise ValueError("reversed news window: ...")`                                                                                            |
| `data.providers.news.alpha_vantage.fetch` (if still present)            | Same reversed-window `return []`                                                   | Same fix (skip task if Plan 08 deleted the file)                                                                                           |
| `data.providers.news.tiingo.fetch`                                      | `os.getenv` + `return []` if unset                                                 | `data.secrets.require_key("TIINGO_API_KEY")` (raises `SecretMissingError`); tests assert the raise                                          |
| `data.providers.politician_trades.quiver.fetch`                         | Same `os.getenv` + `return []`                                                     | `require_key("QUIVER_QUANT_API_KEY")`; raises                                                                                              |
| `data.providers.politician_trades.fmp.fetch`                            | Same                                                                               | `require_key("FMP_API_KEY")`; raises                                                                                                       |

---

## Ordered changes (rationale)

1. **Trading212 await fix (A-003) + unknown-instrument fix (A-004)** — smallest blast radius (no live broker in use, no infra), and the fix is a single-character delete plus a small `raise`. Rewrite the cementing test in the same commit.
2. **Snapshotter loud-fail (A-006) + integration test repoint (A-031)** — paired because the test currently masks the fix; landing one without the other leaves the bug detectable only in production.
3. **Per-provider raise migration (A-007, A-039, A-040)** — applied provider-by-provider so each commit is reviewable in isolation. Each one is followed by checking its analyst consumer for a `try/except` that needs widening or removal.

All tasks: British English spellings in identifiers, comments, and prose. Each new function gets a docstring per user-global style.

---

## Task 1: Trading212 — drop `await resp.json()` and rewrite cementing test (A-003)

**Files:**
- Modify: `src/broker/trading212.py:58,77,92,100`
- Modify: `tests/unit/test_trading212_request_construction.py:9-50`

- [ ] **Step 1: Read the current httpx behaviour from docs to confirm `Response.json` is sync.**

`httpx.Response.json()` is synchronous on `httpx.AsyncClient` responses; only `Response.aread()` / `aiter_*` are coroutines. The current `await resp.json() if callable(...)` line works only because `AsyncMock.json` is itself a coroutine, papering over the bug in tests.

- [ ] **Step 2: Write the failing test that demonstrates the real-httpx behaviour.**

Add to `tests/unit/test_trading212_request_construction.py`:

```python
from unittest.mock import AsyncMock, MagicMock

import pytest

from broker.trading212 import Trading212Broker


@pytest.mark.asyncio
async def test_submit_market_does_not_await_sync_json():
    """Real httpx returns a dict (sync) from .json(); awaiting it raises TypeError.

    Cementing-test fix: previous tests set ``client.post.return_value.json =
    AsyncMock(...)`` which papered over the bug. Use ``MagicMock`` here so
    ``.json()`` returns a plain dict, exactly like real httpx.
    """
    response = MagicMock()
    response.raise_for_status = MagicMock(return_value=None)
    response.json = MagicMock(return_value={
        "id": "abc-123",
        "instrumentCode": "AAPL_US_EQ",
        "filledQuantity": 1.5,
        "filledPrice": 200.0,
    })

    client = MagicMock()
    client.post = AsyncMock(return_value=response)  # only the HTTP verb is async

    b = Trading212Broker(
        mode="paper", api_key="K",
        http_client=client, instrument_map={"AAPL": "AAPL_US_EQ"},
    )
    fill = await b.submit_market("AAPL", "BUY", 1.5)

    assert fill.price == 200.0
    assert fill.quantity == 1.5
```

- [ ] **Step 3: Run and verify it fails.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trading212_request_construction.py::test_submit_market_does_not_await_sync_json -v`

Expected: FAIL with `TypeError: object dict can't be used in 'await' expression`.

- [ ] **Step 4: Apply the fix — drop the `await` and the `callable(getattr(...))` hedge at four sites.**

In `src/broker/trading212.py` replace each of the four lines:

```python
data = await resp.json() if callable(getattr(resp, "json", None)) else resp.json()
```

with the straight sync call:

```python
# httpx.Response.json() is synchronous even on AsyncClient.  The previous
# "await ... if callable(...)" hedge papered over an AsyncMock-shaped test
# and would TypeError against real httpx.
data = resp.json()
```

Replace the analogous lines at:
- Line 58 (`submit_market` → `data = ...`)
- Line 77 (`position_size` → `data = ...`)
- Line 92 (`get_portfolio` → `acct_data = ...`)
- Line 100 (`get_portfolio` → `items = ...`)

Each one keeps its local variable name (`data`, `acct_data`, `items`).

- [ ] **Step 5: Rewrite the existing cementing tests to use `MagicMock` for `.json`.**

In `tests/unit/test_trading212_request_construction.py`, change `test_buy_constructs_correct_request` and `test_sell_uses_negative_quantity`. Pattern: replace `client.post.return_value.json = AsyncMock(return_value={...})` with:

```python
response = MagicMock()
response.raise_for_status = MagicMock(return_value=None)
response.json = MagicMock(return_value={
    "id": "abc-123",
    "instrumentCode": "AAPL_US_EQ",
    "filledQuantity": 1.5,
    "filledPrice": 200.0,
})
client = MagicMock()
client.post = AsyncMock(return_value=response)
```

Update both `_buy_*` and `_sell_*` tests to follow this shape.

- [ ] **Step 6: Run the full broker test file and verify all pass.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trading212_request_construction.py -v`

Expected: all tests PASS (new test plus rewritten existing tests).

- [ ] **Step 7: Commit.**

```bash
git add src/broker/trading212.py tests/unit/test_trading212_request_construction.py
git commit -m "fix(broker): drop await on sync httpx Response.json (A-003)

Trading212Broker awaited a synchronous method; tests papered over the bug
with AsyncMock.json. Drop the await at four call sites and rewrite the
cementing tests to use MagicMock for .json so they fail against the real
httpx contract.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: Trading212 — raise on unknown T212 instrument codes (A-004)

**Files:**
- Modify: `src/broker/trading212.py:104-115`
- Test: `tests/unit/test_trading212_request_construction.py`

- [ ] **Step 1: Write the failing test.**

Add to `tests/unit/test_trading212_request_construction.py`:

```python
@pytest.mark.asyncio
async def test_get_portfolio_raises_on_unknown_instrument_code():
    """T212 may return positions in instruments the local map does not know
    about (instrument map stale).  Silently dropping them shrinks the
    portfolio that concentration clamps + BUY→SELL bridge see, causing
    over-allocation.  The fix raises BrokerRejection listing the offenders.
    """
    cash_resp = MagicMock()
    cash_resp.raise_for_status = MagicMock(return_value=None)
    cash_resp.json = MagicMock(return_value={"free": 5_000.0})

    port_resp = MagicMock()
    port_resp.raise_for_status = MagicMock(return_value=None)
    port_resp.json = MagicMock(return_value=[
        {"ticker": "AAPL_US_EQ", "quantity": 1.0,
         "averagePrice": 100.0, "currentPrice": 110.0},
        # Unknown instrument code — not in the local instrument_map.
        {"ticker": "XYZ_US_EQ",  "quantity": 5.0,
         "averagePrice": 50.0,  "currentPrice": 55.0},
    ])

    client = MagicMock()
    client.get = AsyncMock(side_effect=[cash_resp, port_resp])

    b = Trading212Broker(
        mode="paper", api_key="K",
        http_client=client, instrument_map={"AAPL": "AAPL_US_EQ"},
    )

    from broker.protocol import BrokerRejection
    with pytest.raises(BrokerRejection, match="XYZ_US_EQ"):
        await b.get_portfolio()
```

- [ ] **Step 2: Run and verify it fails.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trading212_request_construction.py::test_get_portfolio_raises_on_unknown_instrument_code -v`

Expected: FAIL — current code silently `continue`s.

- [ ] **Step 3: Apply the fix in `src/broker/trading212.py`.**

Replace the `for it in items:` loop in `get_portfolio` (lines 104-113) with:

```python
# Reverse the instrument map so we can convert T212 codes back to tickers.
rev = {v: k for k, v in self._instruments.items()}

# Detect unknown instrument codes up-front and raise so concentration
# clamps + BUY→SELL bridge cannot operate on a silently-shrunken
# portfolio.  A stale instrument_map is a deployment bug, not a
# per-position degradation.
unknown_codes = [it["ticker"] for it in items if it["ticker"] not in rev]
if unknown_codes:
    raise BrokerRejection(
        f"Trading 212 returned positions for unknown instrument codes: "
        f"{sorted(unknown_codes)}. Refresh instrument_map at startup."
    )

positions: dict[str, Position] = {}
for it in items:
    code = it["ticker"]
    positions[rev[code]] = Position(
        quantity   = float(it["quantity"]),
        avg_cost   = float(it["averagePrice"]),
        last_price = float(it["currentPrice"]),
    )
```

- [ ] **Step 4: Run the failing test and verify it passes.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trading212_request_construction.py::test_get_portfolio_raises_on_unknown_instrument_code -v`

Expected: PASS.

- [ ] **Step 5: Run the whole broker test file.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trading212_request_construction.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit.**

```bash
git add src/broker/trading212.py tests/unit/test_trading212_request_construction.py
git commit -m "fix(broker): raise on unknown T212 instrument codes (A-004)

Silently dropping unknown codes shrank the portfolio seen by concentration
clamps and the BUY→SELL bridge. Raise BrokerRejection listing offenders so
a stale instrument_map fails the tick at the boundary.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: Snapshotter — raise on first-tick SPY-fetch failure; loud warn on subsequent ticks (A-006)

**Files:**
- Modify: `src/agents/snapshot/agent.py:60-74`
- Test: `tests/integration/test_snapshotter.py` (additional test, full repoint in Task 4)

- [ ] **Step 1: Write a failing test for the first-tick loud-fail behaviour.**

Add to `tests/integration/test_snapshotter.py`:

```python
@pytest.mark.asyncio
async def test_snapshotter_raises_when_spy_fetch_fails_on_first_tick():
    """First tick anchors spy_start_price; a 0.0 anchor permanently
    invalidates every subsequent return calc.  The snapshotter must
    raise rather than anchor at 0.0.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    state = {"tick_id": "tick-001"}                # no spy_start_price yet
    ctx = _make_ctx(state)

    with patch("data.get_price_history",
               side_effect=RuntimeError("spy upstream down")):
        with pytest.raises(RuntimeError, match="spy upstream down"):
            async for _ in snapper._run_async_impl(ctx):
                pass


@pytest.mark.asyncio
async def test_snapshotter_reuses_prior_anchor_when_spy_fetch_fails_later():
    """Subsequent ticks log loud WARNING and reuse the prior anchor;
    never silently substitute 0.0.
    """
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    state = {
        "tick_id":          "tick-002",
        "starting_capital": 10_000.0,
        "spy_start_price":  470.0,
        "last_spy_price":   480.0,        # carried from prior tick
    }
    ctx = _make_ctx(state)

    with patch("data.get_price_history",
               side_effect=RuntimeError("transient")):
        async for _ in snapper._run_async_impl(ctx):
            pass

    snap = state["last_snapshot"]
    # Anchor preserved; spy_price falls back to last good value, never 0.0.
    assert snap["spy_price"] == 480.0
```

- [ ] **Step 2: Run and verify both fail.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_snapshotter.py::test_snapshotter_raises_when_spy_fetch_fails_on_first_tick tests/integration/test_snapshotter.py::test_snapshotter_reuses_prior_anchor_when_spy_fetch_fails_later -v`

Expected: FAIL — current code silently substitutes `0.0`.

- [ ] **Step 3: Apply the fix in `src/agents/snapshot/agent.py`.**

Replace lines 60-74 (the `spy_price = 0.0 / try/except` block) with:

```python
# Fetch the latest SPY close via the registered price-history provider.
# A bare `except: spy_price = 0.0` silently destroys every return calc
# because the first tick anchors spy_start_price; a 0.0 anchor turns
# every subsequent `(spy_price - 0) / 0 * 100` into nonsense.
#
# Policy:
#   • First tick (no spy_start_price yet) — raise, since the anchor is
#     load-bearing and cannot be reconstructed later.
#   • Subsequent ticks — log a WARNING with traceback and reuse
#     state["last_spy_price"] (the prior tick's value).  Never silently
#     substitute 0.0.
from data import get_price_history

tick_phase = state.get("tick_phase")
first_tick = "spy_start_price" not in state

try:
    spy_hist = await get_price_history(
        "SPY",
        period   = "5d",
        interval = "1d",
        as_of    = recorded_at,
        phase    = tick_phase,
    )
    if not spy_hist.bars:
        raise RuntimeError(
            f"SPY price history returned no bars at as_of={recorded_at.isoformat()}"
        )
    spy_price = float(spy_hist.bars[-1].close)
except Exception:
    if first_tick:
        # Re-raise — anchoring at 0.0 would permanently break the run.
        logger.exception(
            "snapshotter: SPY fetch failed on first tick at %s; refusing to "
            "anchor spy_start_price at 0.0", recorded_at.isoformat(),
        )
        raise
    prior = state.get("last_spy_price")
    if prior is None or float(prior) <= 0.0:
        logger.exception(
            "snapshotter: SPY fetch failed at %s and no prior anchor available",
            recorded_at.isoformat(),
        )
        raise
    logger.warning(
        "snapshotter: SPY fetch failed at %s; reusing last_spy_price=%.4f",
        recorded_at.isoformat(), float(prior), exc_info=True,
    )
    spy_price = float(prior)

# Cache for the next tick's fallback path.
state["last_spy_price"] = spy_price
```

Also ensure a module-level `logger = logging.getLogger(__name__)` exists at the top of the file; add `import logging` and the logger line if absent.

- [ ] **Step 4: Run the two new tests and verify they pass.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_snapshotter.py::test_snapshotter_raises_when_spy_fetch_fails_on_first_tick tests/integration/test_snapshotter.py::test_snapshotter_reuses_prior_anchor_when_spy_fetch_fails_later -v`

Expected: PASS.

- [ ] **Step 5: Run the full snapshotter test file — note `test_snapshotter_accepts_iso_string_as_of` will now FAIL (it tests the silent-fallback path we just removed).**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_snapshotter.py -v`

Expected: `test_snapshotter_accepts_iso_string_as_of` FAILs because it asserts the agent degrades silently. This is intentional — we rewrite it in Task 4 alongside the patch-target repoint.

- [ ] **Step 6: Commit.**

```bash
git add src/agents/snapshot/agent.py tests/integration/test_snapshotter.py
git commit -m "fix(snapshot): raise on first-tick SPY-fetch failure (A-006)

Silently substituting spy_price=0.0 anchored spy_start_price at 0 and
permanently broke every return calc downstream. First tick now raises;
subsequent ticks log a loud WARNING and reuse last_spy_price.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: Snapshotter integration test — repoint patch target and assert positive anchor (A-031)

**Files:**
- Modify: `tests/integration/test_snapshotter.py`

- [ ] **Step 1: Read the existing test to confirm the no-op patch.**

`tests/integration/test_snapshotter.py:31` patches `yfinance.Ticker`. Production calls `data.get_price_history` (snapshotter line 64). The patch never intercepts; `mock_yf` is dead.

- [ ] **Step 2: Rewrite `test_snapshotter_writes_state` to patch the real target and assert `spy_price > 0`.**

Replace the existing test body in `tests/integration/test_snapshotter.py`:

```python
@pytest.mark.asyncio
async def test_snapshotter_writes_state():
    """Snapshot row records SPY anchor + bot total.

    Patches ``data.get_price_history`` (the real call site), not
    ``yfinance.Ticker``.  Asserts spy_price > 0 — a silent 0.0
    anchor would invalidate every subsequent return calc, so the
    test must catch it.
    """
    from data.models.price_history import PriceBar, PriceHistory

    broker  = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    state   = {"tick_id": "tick-001"}
    ctx     = _make_ctx(state)

    fake_history = PriceHistory(
        ticker="SPY",
        bars=[PriceBar(
            timestamp="2026-05-08T20:00:00+00:00",
            open=465.0, high=472.0, low=464.0, close=470.0, volume=1_000_000,
        )],
    )

    async def _fake_get_price_history(*_args, **_kwargs):
        return fake_history

    with patch("data.get_price_history", side_effect=_fake_get_price_history):
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    assert snap["bot_total_value"] == 10_000.0
    assert snap["tick_id"] == "tick-001"
    # Critical: the previous test patched the wrong target and accepted
    # spy_price=0.0, masking A-006.  Assert a real positive anchor.
    assert snap["spy_price"] > 0
    assert snap["spy_price"] == 470.0
```

Adjust the `PriceBar` field names if the dataclass differs — run the test, follow the validation error, and fix the constructor. The intent is a single bar with `close=470.0`.

- [ ] **Step 3: Delete or rewrite `test_snapshotter_accepts_iso_string_as_of`.**

This test asserts the agent degrades silently on provider failure. After Task 3, that path raises on the first tick (which this test exercises). Rewrite it to use a successful patch so it still proves ISO-string `as_of` round-trips:

```python
@pytest.mark.asyncio
async def test_snapshotter_accepts_iso_string_as_of():
    """state["as_of"] arriving as an ISO-8601 string must not raise
    AsOfRequiredError.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter and now passes ``raw_as_of`` directly to ``resolve_as_of``.
    """
    from datetime import datetime

    from data.models.price_history import PriceBar, PriceHistory

    broker  = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    iso_as_of = "2026-05-08T14:00:00+00:00"
    state = {
        "tick_id": "tick-iso",
        "as_of":   iso_as_of,
    }
    ctx = _make_ctx(state)

    fake_history = PriceHistory(
        ticker="SPY",
        bars=[PriceBar(
            timestamp=iso_as_of,
            open=465.0, high=472.0, low=464.0, close=470.0, volume=1_000_000,
        )],
    )

    async def _fake_get_price_history(*_a, **_kw):
        return fake_history

    with patch("data.get_price_history", side_effect=_fake_get_price_history):
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    expected_dt = datetime.fromisoformat(iso_as_of)
    assert isinstance(snap["recorded_at"], str)
    actual_dt = datetime.fromisoformat(snap["recorded_at"])
    assert actual_dt.replace(tzinfo=None) == expected_dt.replace(tzinfo=None)
```

- [ ] **Step 4: Run the full snapshotter integration file.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_snapshotter.py -v`

Expected: all PASS.

- [ ] **Step 5: Commit.**

```bash
git add tests/integration/test_snapshotter.py
git commit -m "fix(tests): repoint snapshotter patch to data.get_price_history (A-031)

Old test patched yfinance.Ticker but production calls data.get_price_history,
so the patch was a no-op and masked A-006. Repoint to the real target and
assert spy_price > 0 so silent 0.0 substitution can no longer slip through.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Finnhub social-sentiment — raise on API errors except documented premium-gate (A-007)

**Files:**
- Modify: `src/data/providers/social_sentiment/finnhub.py:77-85`
- Create: a typed exception in the same module for the premium-gate path
- Modify: `src/agents/analysts/social/fetch.py:57-62` (consumer)
- Test: `tests/unit/data/providers/test_social_sentiment_finnhub.py` (new file)

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/data/providers/test_social_sentiment_finnhub.py`:

```python
"""Boundary tests for the Finnhub social-sentiment provider.

Verifies the provider raises loudly on auth/rate-limit/server errors
rather than returning a synthetic-empty SocialSentiment that downstream
code cannot distinguish from "no mentions".
"""
from unittest.mock import patch

import finnhub
import pytest

from data.providers.social_sentiment import finnhub as provider


@pytest.mark.asyncio
async def test_fetch_raises_on_non_premium_api_exception():
    """A 429 / 500 / auth error must raise, not return empty."""
    err = finnhub.FinnhubAPIException("429 rate limited")
    with patch.object(provider, "_fetch_social", side_effect=err):
        with pytest.raises(finnhub.FinnhubAPIException):
            await provider.fetch("AAPL", as_of=None)


@pytest.mark.asyncio
async def test_fetch_raises_premium_gated_on_403():
    """A 403 (free-tier premium gate) raises a typed PremiumGatedError so
    callers may choose to soft-fail explicitly.  No silent empty fallback.
    """
    err = finnhub.FinnhubAPIException("API limit reached. Please use a higher rate limit (403)")
    with patch.object(provider, "_fetch_social", side_effect=err):
        with pytest.raises(provider.PremiumGatedError):
            await provider.fetch("AAPL", as_of=None)
```

- [ ] **Step 2: Run and verify it fails.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_social_sentiment_finnhub.py -v`

Expected: FAIL — current code returns empty on every exception, and `PremiumGatedError` does not exist.

- [ ] **Step 3: Add the typed exception and replace the swallow with explicit branching.**

In `src/data/providers/social_sentiment/finnhub.py`, add near the top:

```python
class PremiumGatedError(RuntimeError):
    """Raised when Finnhub returns a 403 on the premium-only social
    sentiment endpoint.  Distinct from arbitrary API errors so consumers
    may choose to soft-fail this specific case without masking real
    auth/rate-limit/server failures.
    """
```

Replace the `try` block at lines 77-85:

```python
try:
    payload = await asyncio.to_thread(_fetch_social, symbol)
except finnhub.FinnhubAPIException as exc:
    # The premium-only endpoint returns 403 on the free tier.  We promote
    # exactly that condition to a typed PremiumGatedError; every other
    # API error (auth, 429, 5xx) raises through so the operator notices.
    msg = str(exc)
    if "403" in msg or "premium" in msg.lower():
        raise PremiumGatedError(
            f"social_sentiment/finnhub: premium-gated for {symbol} ({exc})"
        ) from exc
    raise
```

- [ ] **Step 4: Update the social consumer to catch the typed gate explicitly.**

In `src/agents/analysts/social/fetch.py`, change the `try/except` at lines 57-62 to:

```python
try:
    sentiment = await get_social_sentiment(ticker, as_of=as_of)
except Exception as exc:
    # Premium-gate is the only documented soft-fail path; anything else
    # is a real provider failure and the warning should surface it.
    from data.providers.social_sentiment.finnhub import PremiumGatedError
    if isinstance(exc, PremiumGatedError):
        logger.info("social_sentiment premium-gated for %s", ticker)
    else:
        logger.warning("social_sentiment fetch failed for %s: %s", ticker, exc)
    sentiment = None
```

(The consumer continues to degrade per-ticker because the social analyst is non-load-bearing for a single missing ticker; the *provider* boundary is now honest.)

- [ ] **Step 5: Run new tests + existing consumer tests.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_social_sentiment_finnhub.py tests/ -k social -v`

Expected: new tests PASS; pre-existing social tests still PASS (the consumer still handles both shapes).

- [ ] **Step 6: Commit.**

```bash
git add src/data/providers/social_sentiment/finnhub.py src/agents/analysts/social/fetch.py tests/unit/data/providers/test_social_sentiment_finnhub.py
git commit -m "fix(social_sentiment): raise on non-premium Finnhub errors (A-007)

Previously every FinnhubAPIException returned an empty SocialSentiment,
indistinguishable downstream from \"no mentions\". Add typed
PremiumGatedError for the documented 403 free-tier path; raise on
everything else so auth/rate-limit/server failures surface.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 6: News providers — raise on reversed window (A-039)

**Files:**
- Modify: `src/data/providers/news/finnhub.py:344-359`
- Modify: `src/data/providers/news/alpha_vantage.py:340-344` (if file still exists; skip otherwise)
- Test: `tests/unit/data/providers/test_news_finnhub_window.py` (new)

- [ ] **Step 1: Write the failing test.**

Create `tests/unit/data/providers/test_news_finnhub_window.py`:

```python
"""News-provider boundary tests — reversed windows must raise, not return []."""
from datetime import datetime, timezone

import pytest

from data.providers.news import finnhub as provider


@pytest.mark.asyncio
async def test_fetch_raises_on_reversed_window():
    """from_date > to_date is a caller bug; silently returning [] hides it
    until a backtest produces an inexplicably empty newsfeed.
    """
    as_of = datetime(2026, 3, 15, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="reversed news window"):
        await provider.fetch(
            "AAPL",
            from_date = datetime(2026, 3, 10, tzinfo=timezone.utc),
            to_date   = datetime(2026, 3, 5,  tzinfo=timezone.utc),  # before from
            as_of     = as_of,
        )
```

- [ ] **Step 2: Run and verify it fails.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_news_finnhub_window.py -v`

Expected: FAIL — current code `return []`.

- [ ] **Step 3: Apply the fix in `src/data/providers/news/finnhub.py`.**

Replace lines 346-359 (the two `return []` clauses) with:

```python
# Defensive: caller-supplied bounds must coerce cleanly.  Garbage in →
# loud raise rather than silently empty results.
if window_start is None or explicit_end is None:
    raise ValueError(
        f"news.finnhub: could not coerce window bounds "
        f"from_date={from_date!r} to_date={to_date!r}"
    )

# Upper bound: caller's ``to_date``, but never past ``as_of`` — this is
# the provider's last-line-of-defence PIT cap.
window_end = min(explicit_end, as_of_date)

# Reversed window (``from_date > to_date`` after clipping) is a caller
# bug.  The previous ``return []`` hid backtest mis-windowing for hours.
if window_start > window_end:
    raise ValueError(
        f"news.finnhub: reversed news window for {symbol}: "
        f"window_start={window_start.isoformat()} > window_end={window_end.isoformat()} "
        f"(from_date={from_date}, to_date={to_date}, as_of={as_of_date})"
    )
```

- [ ] **Step 4: Apply the same fix to `src/data/providers/news/alpha_vantage.py` IF the file still exists.**

Check first:

```bash
test -f src/data/providers/news/alpha_vantage.py && echo PRESENT || echo DELETED
```

If `PRESENT`: replace its reversed-window `return []` at lines 340-344 with a `raise ValueError` mirroring the finnhub message, substituting `news.alpha_vantage` for the prefix. If `DELETED` (Plan 08 landed first): skip — no work.

- [ ] **Step 5: Run new test and existing news tests.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k news_finnhub -v`

Expected: new test PASSes. Any pre-existing test that asserted `[]` on reversed window must be rewritten to `pytest.raises(ValueError)` — there are no known cementing tests for this path but check and fix any that surface.

- [ ] **Step 6: Commit.**

```bash
git add src/data/providers/news/finnhub.py tests/unit/data/providers/test_news_finnhub_window.py
# Add alpha_vantage.py to the staged set only if Step 4 modified it.
git commit -m "fix(news): raise on reversed news window (A-039)

Silent return-empty on from_date > to_date hid backtest mis-windowing.
Raise ValueError listing the offending bounds so the caller fails fast.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 7: News + politician_trades providers — raise on missing API key via `require_key` (A-040)

**Files:**
- Modify: `src/data/providers/news/tiingo.py:147-150`
- Modify: `src/data/providers/politician_trades/quiver.py:153-158`
- Modify: `src/data/providers/politician_trades/fmp.py:251-258`
- Test: `tests/unit/data/providers/test_secret_required_at_boundary.py` (new)

- [ ] **Step 1: Read the existing `require_key` helper.**

`src/data/secrets.py` already provides `require_key(env_var: str) -> str` which raises `SecretMissingError(RuntimeError)` on unset/empty. Use it — do not invent a new mechanism.

- [ ] **Step 2: Write the failing test.**

Create `tests/unit/data/providers/test_secret_required_at_boundary.py`:

```python
"""Verify providers raise SecretMissingError when their API key is unset.

Previously these providers returned [] on missing key, which is
indistinguishable downstream from "no data" and hid mis-configuration.
"""
from datetime import datetime, timezone

import pytest

from data.secrets import SecretMissingError


@pytest.mark.asyncio
async def test_tiingo_news_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    from data.providers.news import tiingo
    with pytest.raises(SecretMissingError, match="TIINGO_API_KEY"):
        await tiingo.fetch(
            "AAPL",
            from_date = datetime(2026, 3, 1,  tzinfo=timezone.utc),
            to_date   = datetime(2026, 3, 10, tzinfo=timezone.utc),
            as_of     = datetime(2026, 3, 10, tzinfo=timezone.utc),
        )


@pytest.mark.asyncio
async def test_quiver_politician_trades_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("QUIVER_QUANT_API_KEY", raising=False)
    from data.providers.politician_trades import quiver
    with pytest.raises(SecretMissingError, match="QUIVER_QUANT_API_KEY"):
        await quiver.fetch(
            "AAPL",
            as_of         = datetime(2026, 3, 10, tzinfo=timezone.utc),
            lookback_days = 30,
        )


@pytest.mark.asyncio
async def test_fmp_politician_trades_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    from data.providers.politician_trades import fmp
    with pytest.raises(SecretMissingError, match="FMP_API_KEY"):
        await fmp.fetch(
            "AAPL",
            as_of         = datetime(2026, 3, 10, tzinfo=timezone.utc),
            lookback_days = 30,
        )
```

- [ ] **Step 3: Run and verify all three fail.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_secret_required_at_boundary.py -v`

Expected: FAIL — current code returns `[]`.

- [ ] **Step 4: Apply the fix to `src/data/providers/news/tiingo.py`.**

Replace lines 147-150:

```python
api_key = os.getenv("TIINGO_API_KEY")
if not api_key:
    logger.debug("TIINGO_API_KEY unset — fetch returning []")
    return []
```

with:

```python
# Raise loudly via require_key.  Returning [] here was indistinguishable
# from "no articles" downstream and hid mis-configuration in fresh dev
# environments.  Callers that want to soft-fail must catch SecretMissingError
# explicitly.
from data.secrets import require_key
api_key = require_key("TIINGO_API_KEY")
```

Update the docstring `Returns` block in the same function from `"or [] if TIINGO_API_KEY is unset"` to `"Parsed articles. Raises SecretMissingError if TIINGO_API_KEY is unset."`.

- [ ] **Step 5: Apply the fix to `src/data/providers/politician_trades/quiver.py`.**

Replace lines 153-158 with:

```python
from data.secrets import require_key
api_key = require_key("QUIVER_QUANT_API_KEY")
```

Update the function's docstring soft-fail note accordingly.

- [ ] **Step 6: Apply the fix to `src/data/providers/politician_trades/fmp.py`.**

Replace lines 251-258 with:

```python
from data.secrets import require_key
api_key = require_key("FMP_API_KEY")

symbol = (ticker or "").upper()
if not symbol:
    # Empty ticker is a caller bug, not an API-key issue.
    raise ValueError("fmp.politician_trades: ticker is required and was empty")
```

(Promotes the previously-silent empty-ticker `return []` to a `ValueError` for the same loud-fail reason; affects only callers passing empty strings, which is a bug.)

- [ ] **Step 7: Run the new tests.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_secret_required_at_boundary.py -v`

Expected: all PASS.

- [ ] **Step 8: Identify and rewrite cementing tests that assert the old soft-fail.**

Run:

```bash
grep -rn "TIINGO_API_KEY\|QUIVER_QUANT_API_KEY\|FMP_API_KEY" tests/ | grep -v __pycache__
```

For every test that exercises the unset-key path and asserts `result == []` or similar, rewrite the assertion to `pytest.raises(SecretMissingError)`. If a test is purely a cementing test for the swallow (no other coverage value), delete it.

- [ ] **Step 9: Check the politician_trades analyst still degrades gracefully.**

Per project memory, `politician_trades` is intentionally disabled in `_build_provider_fns` and the analyst should already degrade gracefully. Verify in `src/agents/analysts/smart_money/fetch.py:108-114` that the `try/except Exception` wrapping `get_public_figure_trades` still catches `SecretMissingError` (subclass of `RuntimeError`, which is itself an `Exception`) — it does, but confirm by reading the file.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k smart_money -v`

Expected: smart_money tests still PASS — the analyst boundary still degrades; only the *provider* boundary is now honest.

- [ ] **Step 10: Run the full provider+analyst test sweep.**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data tests/analysts -v`

Expected: all PASS.

- [ ] **Step 11: Commit.**

```bash
git add src/data/providers/news/tiingo.py src/data/providers/politician_trades/quiver.py src/data/providers/politician_trades/fmp.py tests/unit/data/providers/test_secret_required_at_boundary.py
# plus any rewritten/deleted cementing tests from Step 8
git commit -m "fix(providers): raise SecretMissingError on missing API key (A-040)

tiingo, quiver, and fmp providers silently returned [] on missing keys,
indistinguishable downstream from genuine \"no data\" and hiding fresh-env
mis-configuration. Route through data.secrets.require_key so the boundary
raises. The smart_money analyst already catches per-ticker exceptions,
preserving the documented soft-fail for politician_trades.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Test strategy summary

| Boundary                                  | Fails-before test                                                                          | Passes-after assertion                                                                                                          | Cementing-test rewrite                                              |
| ----------------------------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| T212 sync `.json()` (A-003)              | `test_submit_market_does_not_await_sync_json` — `MagicMock` for `.json`; raises `TypeError` today | Same test passes; no `await` on `dict`                                                                                          | `test_buy_constructs_correct_request`, `test_sell_uses_negative_quantity` switched to `MagicMock` |
| T212 unknown instrument (A-004)          | `test_get_portfolio_raises_on_unknown_instrument_code`                                     | `BrokerRejection` raised listing the offending codes                                                                            | None pre-existing                                                   |
| Snapshotter SPY (A-006)                  | `test_snapshotter_raises_when_spy_fetch_fails_on_first_tick` + reuse-anchor test          | First tick raises; later ticks reuse `last_spy_price`; `spy_price` never silently 0.0                                            | Rewrite `test_snapshotter_accepts_iso_string_as_of` (Task 4)        |
| Snapshotter patch target (A-031)         | `assert snap["spy_price"] > 0` in rewritten `test_snapshotter_writes_state`              | Patch on `data.get_price_history` intercepts; spy_price > 0                                                                     | Replaces the no-op `yfinance.Ticker` patch                          |
| Finnhub social (A-007)                    | `test_fetch_raises_on_non_premium_api_exception` + premium-gate test                     | `FinnhubAPIException` propagates; 403 → typed `PremiumGatedError`. `[]` is no longer a valid return from this provider.          | None pre-existing                                                   |
| News reversed window (A-039)              | `test_fetch_raises_on_reversed_window`                                                    | `ValueError("reversed news window: ...")` — `[]` no longer a valid return for reversed windows.                                  | None known; sweep `tests/ -k news` during Task 6 to confirm         |
| Missing API key (A-040)                   | `test_{tiingo,quiver,fmp}_raises_without_api_key`                                          | `SecretMissingError` — `[]` no longer a valid return when key is unset.                                                          | Step 8 sweep finds & rewrites any cementing tests                   |

**Universal assertion:** every provider task includes a test that proves `[]` (or `0.0`, or `None`) is no longer the return path for the failure case it covers. This is the standing claim later plans depend on.

---

## Risks / silent-regression checklist

When a provider stops degrading, every consumer that used to receive `[]` now receives an exception. Audit each consumer before merging:

- [ ] **News analyst (`src/agents/analysts/news/fetch_agent.py:80-85`)** — already wraps `await get_stock_news(...)` in `try/except Exception → articles = []`. Continues to degrade per-ticker. **No change required**, but verify the warning log fires cleanly with the new `ValueError` / `SecretMissingError`.
- [ ] **Smart-money analyst (`src/agents/analysts/smart_money/fetch.py:108-114, 116-125`)** — already wraps `politician_trades` and `notable_holders` fetches in `try/except Exception → []`. `SecretMissingError` subclasses `RuntimeError → Exception` so it is caught. **Project memory: politician_trades is intentionally disabled in the fetcher — do not break that path.** Confirm the analyst still emits a `no_data` verdict for tickers with no smart-money data; do not change the analyst's contract.
- [ ] **Social analyst (`src/agents/analysts/social/fetch.py`)** — Task 5 already updates this consumer to recognise the typed `PremiumGatedError`. Verify both branches log distinctly.
- [ ] **Snapshotter consumers** — `state["last_snapshot"]` is read by reporting and backtest decision logger. The new `last_spy_price` key is additive and harmless to ignore; verify no downstream `KeyError` if older state shapes are loaded mid-run (backtest resumes a fresh state per window, so this is safe).
- [ ] **Trading212** — no live caller exists yet (pre-deployment); the only consumer is tests. Confirm `tests/unit/test_trading212_*.py` is the complete blast radius.
- [ ] **Registry swap** — `src/backtest/runner.py:450` swaps every domain to `"cache"`. Verify `("price_history", "cache")`, `("news", "cache")`, etc. are all in `_REGISTRY` before merging — if not, this plan will break backtest entry. (They are, per `src/backtest/providers/*_cache.py`.)

**Do NOT, in this plan:**
- Touch `src/agents/risk_gate/` (Plan 02).
- Touch `src/agents/executor/agent.py` (Plan 03).
- Delete any provider module — Plan 08 owns dead-code deletion; this plan only fixes behaviour in place.
- Add new abstractions (a "loud-fail mixin", a "raise-or-empty" decorator, etc.). Each fix is local.

---

## Definition of done

- [ ] All seven findings (A-003, A-004, A-006, A-007, A-031, A-039, A-040) have a corresponding code change and a failing-before / passing-after test pair landed. (A-041 was originally listed here but is owned by Plan 08; the duplicate task has been removed.)
- [ ] `git grep -nE "return \[\]\s*$" src/data/providers/` returns no hits in the touched modules' failure paths. (Successful empty results — e.g. a real upstream returning zero rows — remain legitimate; the grep is a sanity check, read the surrounding context before claiming a regression.)
- [ ] `git grep -n "await resp.json()" src/broker/` returns zero hits.
- [ ] `git grep -n "spy_price = 0.0" src/agents/snapshot/` returns zero hits.
- [ ] `git grep -n "yfinance.Ticker" tests/integration/test_snapshotter.py` returns zero hits.
- [ ] `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_trading212_request_construction.py tests/integration/test_snapshotter.py tests/unit/data/providers tests/analysts -v` is green.
- [ ] `PYTHONPATH=src .venv/bin/python -m ruff check src/` is green for every touched file.
- [ ] Each commit message references its finding ID (`A-003`, etc.) so the audit can be closed by `git log --grep`.
- [ ] No code in `src/agents/risk_gate/`, `src/agents/executor/`, or `src/orchestrator/` was modified (those are other plans' scopes).
- [ ] `politician_trades` analyst still emits `no_data` for unconfigured tickers and the smart-money pipeline still passes its existing tests — verifying the memory-noted soft-fail is preserved at the analyst boundary.

---

## Self-review

**Spec coverage:** A-003 (Task 1), A-004 (Task 2), A-006 (Task 3), A-007 (Task 5), A-031 (Task 4), A-039 (Task 6), A-040 (Task 7). Seven findings → seven tasks. Complete. (A-041 was originally listed here but is owned by Plan 08; the duplicate task has been removed.)

**Placeholder scan:** every code step shows the actual replacement. No `TODO`, no `add appropriate error handling`. The one conditional ("apply same fix to `alpha_vantage.py` IF still present") is gated on a concrete shell check, not on engineer judgement.

**Type consistency:** `BrokerRejection` (imported from `broker.protocol`) used in Tasks 1 and 2; `SecretMissingError` (from `data.secrets`) used consistently in Task 7; new `PremiumGatedError` introduced in Task 5 and referenced in the same task's consumer update. `require_key` signature matches `src/data/secrets.py`. `PriceHistory`/`PriceBar` constructor in Task 4 is gated by a "follow the validation error" note in case the dataclass field names differ.
