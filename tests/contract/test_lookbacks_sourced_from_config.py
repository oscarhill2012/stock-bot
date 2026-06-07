"""Contract test: every per-domain fetch knob comes from ``get_config().defaults``.

Patches the data-config singleton with sentinel values for every fetch knob
exposed in ``FetchDefaults``, then asserts that:

- Each analyst fetch callback forwards the sentinels to its dispatcher.
- Each backtest cache-fill closure (``scripts.backtest_fetch._build_provider_fns``)
  forwards the sentinels to its dispatcher.

Catches any regression where a caller re-introduces a hardcoded constant,
silently swallows the config value, or fails to wire a kwarg at all.  This
test is the safety net behind the Phase 7.5 "config-as-truth" invariant.

The sentinels are deliberately well outside any plausible production range
so an assertion failure reports an obviously wrong number rather than one
that happens to collide with a real default.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from data.config import DataConfig, FetchDefaults

# ---------------------------------------------------------------------------
# Sentinel values — chosen to be distinct from every plausible production value.
# ---------------------------------------------------------------------------

SENTINEL_NEWS                 = 991
SENTINEL_INSIDER              = 993
SENTINEL_POLITICIAN           = 995
SENTINEL_NOTABLE_HOLDER       = 997
SENTINEL_NOTABLE_HOLDER_LIMIT = 901
SENTINEL_FILINGS_PER_FORM     = 903
SENTINEL_FILINGS_LOOKBACK     = 905
# include_filing_excerpts is bool — sentinel uses ``False`` (non-default of True).
SENTINEL_INCLUDE_EXCERPTS     = False


def _sentinel_config() -> DataConfig:
    """Build a ``DataConfig`` whose every fetch knob carries an obvious sentinel.

    Returns
    -------
    DataConfig
        A fully-valid ``DataConfig`` instance where every lookback / limit /
        boolean knob in ``FetchDefaults`` is a unique sentinel.  Used by every
        contract test in this module via ``monkeypatch.setattr`` on
        ``data.config._cache``.
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
        },
        defaults=FetchDefaults(
            news_lookback_days           = SENTINEL_NEWS,
            insider_lookback_days        = SENTINEL_INSIDER,
            politician_lookback_days     = SENTINEL_POLITICIAN,
            notable_holder_lookback_days = SENTINEL_NOTABLE_HOLDER,
            notable_holder_limit         = SENTINEL_NOTABLE_HOLDER_LIMIT,
            filings_per_form             = SENTINEL_FILINGS_PER_FORM,
            include_filing_excerpts      = SENTINEL_INCLUDE_EXCERPTS,
            filings_lookback_days        = SENTINEL_FILINGS_LOOKBACK,
        ),
        quiver_http_timeout_seconds = 15.0,
    )


# ---------------------------------------------------------------------------
# Analyst callbacks — smart_money + fundamental
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_smart_money_fetch_uses_config_lookbacks_and_limit(monkeypatch) -> None:
    """``smart_money_fetch_callback`` forwards every config sentinel to its providers.

    Replaces the ``_cache`` singleton in ``data.config`` with a sentinel
    ``DataConfig``, then replaces the provider functions bound in
    ``smart_money.fetch`` with lightweight stubs that record the kwargs they
    receive.  Asserts that the recorded ``lookback_days`` for politicians,
    and both ``lookback_days`` and ``limit`` for notable holders, match the
    sentinels — i.e. nothing in the call chain swallows or overrides the
    config value.

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
        captured["politician_lookback"] = lookback_days
        return []

    async def fake_holders(ticker, *, lookback_days, limit, as_of):
        captured["holder_lookback"] = lookback_days
        captured["holder_limit"]    = limit
        return []

    monkeypatch.setattr(smart_money_fetch, "get_public_figure_trades", fake_politicians)
    monkeypatch.setattr(smart_money_fetch, "get_notable_holders",      fake_holders)

    class FakeCtx:
        state = {"tickers": ["AAPL"], "as_of": datetime.now(UTC)}

    await smart_money_fetch.smart_money_fetch_callback(FakeCtx())

    assert captured["politician_lookback"] == SENTINEL_POLITICIAN
    assert captured["holder_lookback"]     == SENTINEL_NOTABLE_HOLDER
    assert captured["holder_limit"]        == SENTINEL_NOTABLE_HOLDER_LIMIT


@pytest.mark.asyncio
async def test_fundamental_fetch_agent_uses_config_insider_and_filings(monkeypatch) -> None:
    """``FundamentalFetchAgent`` forwards every config sentinel for its three domains.

    Phase 9 retired ``fundamental_fetch_callback`` and replaced it with the
    ``FundamentalFetchAgent`` BaseAgent.  This test preserves the same
    config-forwarding contract — the ``as_of`` plumbing and config-sentinel
    pass-through are the meaningful invariant, not which surface carries them.

    Replaces the ``_cache`` singleton with a sentinel ``DataConfig`` and stubs
    out all three provider calls in ``fundamental.fetch_agent``.  Asserts that:

    - The insider stub receives ``lookback_days = SENTINEL_INSIDER``.
    - The filings stub receives ``limit = SENTINEL_FILINGS_PER_FORM`` and
      ``include_excerpts = SENTINEL_INCLUDE_EXCERPTS``.

    This guards against drift where any of the three knobs reverts to a
    hardcoded module constant or a literal default.

    Parameters
    ----------
    monkeypatch:
        pytest ``monkeypatch`` fixture.
    """
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions import InMemorySessionService

    from agents.analysts.fundamental import fetch_agent as fundamental_fetch_agent_mod
    from agents.analysts.fundamental.fetch_agent import FundamentalFetchAgent
    from data import config as data_config_mod
    from data.models import Form4Bundle

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, object] = {}

    async def fake_insider(ticker, *, lookback_days, as_of):
        captured["insider_lookback"] = lookback_days
        return Form4Bundle(trades=[], derivatives=[])

    async def fake_ratios(ticker, *, as_of):
        return None

    async def fake_filings(ticker, *, as_of, limit, include_excerpts):
        captured["filings_limit"]            = limit
        captured["filings_include_excerpts"] = include_excerpts
        return []

    monkeypatch.setattr(fundamental_fetch_agent_mod, "get_insider_trades",  fake_insider)
    monkeypatch.setattr(fundamental_fetch_agent_mod, "get_company_ratios",  fake_ratios)
    monkeypatch.setattr(fundamental_fetch_agent_mod, "get_company_filings", fake_filings)

    # Build an ADK session so ``FundamentalFetchAgent.run_async`` has a real
    # InvocationContext rather than a bare namespace.
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="contract_test",
        user_id="test",
        state={"tickers": ["AAPL"], "as_of": datetime.now(UTC)},
        session_id="cfg-sentinel",
    )

    agent = FundamentalFetchAgent(name="FundamentalFetch")
    ctx = InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-cfg",
        agent=agent,
    )

    # Exhaust the async generator — the agent yields exactly one event.
    _ = [ev async for ev in agent.run_async(ctx)]

    assert captured["insider_lookback"]         == SENTINEL_INSIDER
    assert captured["filings_limit"]            == SENTINEL_FILINGS_PER_FORM
    assert captured["filings_include_excerpts"] is SENTINEL_INCLUDE_EXCERPTS


# ---------------------------------------------------------------------------
# Backtest cache-fill closures — scripts.backtest_fetch._build_provider_fns
# ---------------------------------------------------------------------------
#
# These tests guard the *other* call path that consumes the same config keys.
# The cache-fill closures live in ``scripts.backtest_fetch`` and do their
# data-module imports lazily inside ``_build_provider_fns``, so a fake
# attached to ``data.get_*`` BEFORE the call is captured by the closure.
# That arrangement is exactly what we want: every test below patches the
# config singleton, then patches the dispatchers, then builds the fns and
# calls the closure under test.

_FAKE_START = date(2025, 9, 2)
_FAKE_END   = date(2025, 10, 13)


@pytest.mark.asyncio
async def test_backtest_news_uses_config_lookback(monkeypatch) -> None:
    """Cache-fill ``_news`` extends ``from_date`` by ``defaults.news_lookback_days``.

    Patches the config singleton with the sentinel, then patches
    ``data.get_stock_news`` to capture the ``from_date`` kwarg.  Asserts that
    ``(start - from_date).days == SENTINEL_NEWS`` — i.e. the cache-fill
    pre-window buffer is sourced from config, not a hardcoded constant.
    """
    import data as data_pkg
    from data import config as data_config_mod
    from scripts import backtest_fetch as bf

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, object] = {}

    async def fake_news(ticker, *, from_date, to_date, as_of, limit):
        captured["from_date"] = from_date
        return []

    monkeypatch.setattr(data_pkg, "get_stock_news", fake_news)

    fns = bf._build_provider_fns()
    await fns["news"]("AAPL", start=_FAKE_START, end=_FAKE_END)

    assert (_FAKE_START - captured["from_date"]).days == SENTINEL_NEWS


@pytest.mark.asyncio
async def test_backtest_insider_trades_uses_config_lookback(monkeypatch) -> None:
    """Cache-fill ``_insider_trades`` uses ``defaults.insider_lookback_days`` in its formula.

    The formula is ``(end - start).days + defaults.insider_lookback_days``.
    Patches the config singleton and ``data.get_insider_trades``; asserts the
    captured ``lookback_days`` equals window-span plus the sentinel — i.e.
    the config piece is honoured and not hardcoded.
    """
    import data as data_pkg
    from data import config as data_config_mod
    from data.models import Form4Bundle
    from scripts import backtest_fetch as bf

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, int] = {}

    async def fake_insider(ticker, *, lookback_days, as_of):
        captured["lookback_days"] = lookback_days
        return Form4Bundle(trades=[], derivatives=[])

    monkeypatch.setattr(data_pkg, "get_insider_trades", fake_insider)

    fns = bf._build_provider_fns()
    await fns["insider_trades"]("AAPL", start=_FAKE_START, end=_FAKE_END)

    expected = (_FAKE_END - _FAKE_START).days + SENTINEL_INSIDER
    assert captured["lookback_days"] == expected


@pytest.mark.skip(
    reason=(
        "notable_holders cache-fill is shelved (2026-05-19) — see "
        "scripts/backtest_fetch._build_provider_fns and "
        "src/orchestrator/pipeline._build_analyst_pool.  Unskip together "
        "with re-enabling the domain and the SmartMoney analyst once a "
        "subject-side notable-holders provider lands."
    )
)
@pytest.mark.asyncio
async def test_backtest_notable_holders_uses_config_lookback_and_limit(monkeypatch) -> None:
    """Cache-fill ``_notable_holders`` forwards both lookback and limit from config.

    Patches the config singleton and ``data.get_notable_holders``; asserts the
    captured kwargs equal ``SENTINEL_NOTABLE_HOLDER`` and
    ``SENTINEL_NOTABLE_HOLDER_LIMIT`` respectively.  This will FAIL until
    ``_notable_holders`` is wired to read both keys from config.
    """
    import data as data_pkg
    from data import config as data_config_mod
    from scripts import backtest_fetch as bf

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, int] = {}

    async def fake_holders(ticker, *, lookback_days, limit, as_of):
        captured["lookback_days"] = lookback_days
        captured["limit"]         = limit
        return []

    monkeypatch.setattr(data_pkg, "get_notable_holders", fake_holders)

    fns = bf._build_provider_fns()
    await fns["notable_holders"]("AAPL", start=_FAKE_START, end=_FAKE_END)

    assert captured["lookback_days"] == SENTINEL_NOTABLE_HOLDER
    assert captured["limit"]         == SENTINEL_NOTABLE_HOLDER_LIMIT


@pytest.mark.asyncio
async def test_backtest_filings_uses_config_per_form_and_excerpts(monkeypatch) -> None:
    """Cache-fill ``_filings`` forwards ``filings_per_form`` and ``include_filing_excerpts``.

    The cache-fill closure is expected to read both from config and pass
    them to ``get_company_filings`` as ``limit=`` and ``include_excerpts=``.
    Filings_lookback_days is consumed inside ``get_company_filings`` itself,
    so the cache-fill caller does not need to forward it directly.

    This will FAIL until ``_filings`` is wired to read both keys from config.
    """
    import data as data_pkg
    from data import config as data_config_mod
    from scripts import backtest_fetch as bf

    monkeypatch.setattr(data_config_mod, "_cache", _sentinel_config())

    captured: dict[str, object] = {}

    async def fake_filings(ticker, *, as_of, limit, include_excerpts):
        captured["limit"]            = limit
        captured["include_excerpts"] = include_excerpts
        return []

    monkeypatch.setattr(data_pkg, "get_company_filings", fake_filings)

    fns = bf._build_provider_fns()
    await fns["filings"]("AAPL", start=_FAKE_START, end=_FAKE_END)

    assert captured["limit"]            == SENTINEL_FILINGS_PER_FORM
    assert captured["include_excerpts"] is SENTINEL_INCLUDE_EXCERPTS
