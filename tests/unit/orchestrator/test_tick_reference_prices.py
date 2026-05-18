"""Tests for the reference-price pre-tick populator.

Verifies that ``_build_initial_state`` seeds ``state["reference_prices"]``
with one ``PriceHistory`` per reference symbol (SPY + 11 SPDR sector ETFs).

Tier 1 — no LLM, no ADK runner, no real yfinance calls.
"""
from __future__ import annotations

import asyncio

from broker.fake import FakeBroker
from data.models.price_history import PriceHistory

_REFERENCE_SYMBOLS = (
    "SPY",
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
    "XLI", "XLB", "XLRE", "XLU", "XLC",
)


def test_build_initial_state_populates_reference_prices(monkeypatch):
    """``_build_initial_state`` must stow one PriceHistory per reference symbol
    under ``state["reference_prices"]`` — keyed by ticker symbol string."""
    from orchestrator import tick as mod

    # Construct a fake return value keyed on all reference symbols.
    fake = {sym: PriceHistory(ticker=sym, bars=[]) for sym in _REFERENCE_SYMBOLS}

    async def fake_fetch(symbols, *, as_of, **_):
        return fake

    monkeypatch.setattr(mod, "_fetch_reference_prices", fake_fetch)

    broker = FakeBroker(starting_cash=1_000.0, prices={})
    state = asyncio.run(mod._build_initial_state(broker, "tick-ref", ["AAPL"]))

    assert "reference_prices" in state, "state is missing 'reference_prices' key"
    assert set(state["reference_prices"].keys()) == set(fake.keys()), (
        f"Expected keys {set(fake.keys())}, got {set(state['reference_prices'].keys())}"
    )


def test_build_initial_state_reference_prices_are_json_safe_dicts(monkeypatch):
    """Each value in ``state["reference_prices"]`` must be a JSON-safe dict.

    Pydantic objects can't be persisted via the ADK SqlSessionService (its
    DynamicJSON type calls plain ``json.dumps`` on state without a custom
    encoder), so ``_build_initial_state`` dumps each PriceHistory before it
    enters the state dict.  The technical extractor re-validates back to a
    PriceHistory on the read side.
    """
    from orchestrator import tick as mod

    fake = {sym: PriceHistory(ticker=sym, bars=[]) for sym in _REFERENCE_SYMBOLS}

    async def fake_fetch(symbols, *, as_of, **_):
        return fake

    monkeypatch.setattr(mod, "_fetch_reference_prices", fake_fetch)

    broker = FakeBroker(starting_cash=1_000.0, prices={})
    state = asyncio.run(mod._build_initial_state(broker, "tick-ref2", ["AAPL"]))

    import json

    for sym, ph in state["reference_prices"].items():
        assert isinstance(ph, dict), (
            f"Expected dict for {sym}, got {type(ph).__name__}"
        )
        assert ph.get("ticker") == sym, (
            f"Expected dumped ticker={sym}, got {ph.get('ticker')!r}"
        )
        # Round-trip through json.dumps to prove the value is JSON-safe — i.e.
        # the ADK SqlSessionService persistence path will not raise.
        json.dumps(ph)
