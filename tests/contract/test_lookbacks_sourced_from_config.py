"""Contract test: every analyst lookback comes from get_config().defaults.

Patches the data-config singleton with sentinel lookback values and asserts
both analyst fetch callbacks propagate them to the provider layer.
Catches any regression where a module re-introduces a hardcoded constant
or a literal default.

Currently two tests are xfail-marked.  Tasks 7 and 8 each remove one:

- Task 7 removes the smart_money xfail (after migrating that module).
- Task 8 removes the fundamental xfail (after migrating that module).

The aggregator (get_stock_signal_bundle) is deliberately not tested
here — Phase 7.6 deletes the function entirely.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from data.config import DataConfig, FetchDefaults


# Sentinel values chosen to be distinct from every plausible production value.
SENTINEL_NEWS              = 991
SENTINEL_INSIDER           = 993
SENTINEL_POLITICIAN        = 995
SENTINEL_NOTABLE_HOLDER    = 997


def _sentinel_config() -> DataConfig:
    """Build a DataConfig whose lookback fields are unique sentinels.

    Returns
    -------
    DataConfig
        A fully-valid ``DataConfig`` instance where every lookback field
        carries an obviously-invalid sentinel integer.  Using out-of-range
        values means an assertion failure will report a clearly wrong number
        rather than one that happens to collide with a real default.
    """
    return DataConfig(
        providers={
            "price_history":      "yfinance",
            "company_ratios":     "pit_composite",
            "news":               "alpha_vantage",
            "social_sentiment":   "finnhub",
            "insider_trades":     "edgar",
            "politician_trades":  "fmp",
            "notable_holders":    "edgar",
            "filings":            "edgar",
            "earnings":           "finnhub",
            "analyst_consensus":  "yfinance",
            "short_interest":     "finra",
            "options":            "yfinance",
        },
        defaults=FetchDefaults(
            news_lookback_days           = SENTINEL_NEWS,
            insider_lookback_days        = SENTINEL_INSIDER,
            politician_lookback_days     = SENTINEL_POLITICIAN,
            notable_holder_lookback_days = SENTINEL_NOTABLE_HOLDER,
            notable_holder_limit         = 20,
            history_period               = "1y",
            history_interval             = "1d",
            filings_per_form             = 3,
            include_filing_excerpts      = True,
            earnings_lookback_quarters   = 4,
            short_interest_lookback_days = 90,
        ),
        # Task 12 renames this field to ``quiver_http_timeout_seconds``;
        # update this line in lockstep with the Task 12 edits.
        http_timeout_seconds = 15.0,
    )


@pytest.mark.xfail(strict=True, reason="awaiting Task 7 (smart_money) migration")
@pytest.mark.asyncio
async def test_smart_money_fetch_uses_config_lookbacks(monkeypatch) -> None:
    """smart_money_fetch_callback forwards config sentinels to its providers.

    Replaces the ``_cache`` singleton in ``data.config`` with a sentinel
    ``DataConfig``, then replaces the provider functions bound in
    ``smart_money.fetch`` with lightweight stubs that record the
    ``lookback_days`` argument they receive.  Asserts both recorded values
    match the sentinel.

    Parameters
    ----------
    monkeypatch:
        pytest ``monkeypatch`` fixture.
    """
    from agents.analysts.smart_money import fetch as smart_money_fetch
    from data import config as data_config_mod

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, int] = {}

    async def fake_politicians(ticker, *, lookback_days, as_of):
        captured["politician"] = lookback_days
        return []

    async def fake_holders(ticker, *, lookback_days, as_of):
        captured["holder"] = lookback_days
        return []

    monkeypatch.setattr(smart_money_fetch, "get_public_figure_trades", fake_politicians)
    monkeypatch.setattr(smart_money_fetch, "get_notable_holders",      fake_holders)

    class FakeCtx:
        state = {"tickers": ["AAPL"], "as_of": datetime.now(timezone.utc)}

    await smart_money_fetch.smart_money_fetch_callback(FakeCtx())

    assert captured["politician"] == SENTINEL_POLITICIAN
    assert captured["holder"]     == SENTINEL_NOTABLE_HOLDER


@pytest.mark.xfail(strict=True, reason="awaiting Task 8 (fundamental) migration")
@pytest.mark.asyncio
async def test_fundamental_fetch_uses_config_insider_lookback(monkeypatch) -> None:
    """fundamental_fetch_callback forwards the config insider sentinel.

    Replaces the ``_cache`` singleton in ``data.config`` with a sentinel
    ``DataConfig``, then stubs out all three provider calls in
    ``fundamental.fetch``.  Only the insider stub records its
    ``lookback_days`` argument; the assertion confirms the sentinel value
    was forwarded rather than the hardcoded module constant.

    Parameters
    ----------
    monkeypatch:
        pytest ``monkeypatch`` fixture.
    """
    from agents.analysts.fundamental import fetch as fundamental_fetch
    from data import config as data_config_mod
    from data.models import Form4Bundle

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, int] = {}

    async def fake_insider(ticker, *, lookback_days, as_of):
        captured["insider"] = lookback_days
        return Form4Bundle(trades=[], derivatives=[])

    async def fake_ratios(ticker, *, as_of):
        return None

    async def fake_filings(ticker, *, as_of):
        return []

    monkeypatch.setattr(fundamental_fetch, "get_insider_trades",  fake_insider)
    monkeypatch.setattr(fundamental_fetch, "get_company_ratios",  fake_ratios)
    monkeypatch.setattr(fundamental_fetch, "get_company_filings", fake_filings)

    class FakeCtx:
        state = {"tickers": ["AAPL"], "as_of": datetime.now(timezone.utc)}

    await fundamental_fetch.fundamental_fetch_callback(FakeCtx())

    assert captured["insider"] == SENTINEL_INSIDER
