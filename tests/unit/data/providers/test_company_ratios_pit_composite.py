"""PIT-composite ratios provider — XBRL fundamentals + sliced OHLCV technicals."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from data.models import CompanyRatios, OHLCBar, PriceHistory


def _make_bars(n: int, last_close: float = 175.0) -> list[OHLCBar]:
    """Create ``n`` daily bars ending at 2023-03-14 with ``last_close``."""
    bars: list[OHLCBar] = []
    for i in range(n):
        ts    = datetime(2023, 1, 1, tzinfo=UTC).replace(day=min(i + 1, 28))
        close = last_close - (n - 1 - i) * 0.5
        bars.append(OHLCBar(
            timestamp=ts,
            open=close - 0.5,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=1_000_000.0,
        ))
    return bars


@pytest.mark.asyncio
async def test_pit_composite_returns_filled_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider composes XBRL fundamentals + price-derived technicals."""
    import data.providers.company_ratios.pit_composite as mod

    fake_facts = SimpleNamespace(
        long_name      = "Apple Inc.",
        sector         = "Technology",
        shares_out     = 15_700_000_000.0,
        eps_ttm        = 6.0,
        dps_ttm        = 0.92,
    )
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: fake_facts)
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )
    # Stub the XBRL summary so no EDGAR network call or env-var lookup is made.
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.long_name      == "Apple Inc."
    assert out.sector         == "Technology"
    assert out.last_price     == pytest.approx(175.0)
    assert out.market_cap     == pytest.approx(15_700_000_000.0 * 175.0)
    assert out.trailing_pe    == pytest.approx(175.0 / 6.0)
    assert out.dividend_yield == pytest.approx(0.92 / 175.0)
    assert out.fifty_day_average is not None
    assert out.two_hundred_day_average is not None


@pytest.mark.asyncio
async def test_pit_composite_handles_missing_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty XBRL must yield a model with ``None`` fundamentals, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name=None, sector=None, shares_out=None, eps_ttm=None, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(5, last_close=100.0)),
    )
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.last_price  == pytest.approx(100.0)
    assert out.market_cap  is None
    assert out.trailing_pe is None


@pytest.mark.asyncio
async def test_pit_composite_handles_empty_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty OHLCV must yield ``None`` price-derived fields, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="X Co", sector="X", shares_out=1.0, eps_ttm=1.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=[]),
    )
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: {
        "profit_margin": None, "debt_to_equity": None, "roe": None,
        "revenue_growth_yoy": None, "free_cash_flow": None,
        "peg": None,
    })

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert out.long_name  == "X Co"
    assert out.last_price is None
    assert out.market_cap is None


def test_pit_composite_registers_on_import() -> None:
    import data.providers.company_ratios.pit_composite  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("company_ratios", "pit_composite")]
    assert entry.upstream == "yfinance"   # shares yfinance limiter for price data


# ---------------------------------------------------------------------------
# Task 4.4 — six XBRL-derivable ratios + as_of population
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pit_composite_populates_new_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """All six XBRL-derived ratio fields are populated when the summary returns full data."""
    import data.providers.company_ratios.pit_composite as mod

    # Stub out the XBRL facts (identity / price primitives) so the test is
    # isolated from any EDGAR network call.
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: SimpleNamespace(
        long_name="Apple Inc.", sector="Technology",
        shares_out=15_700_000_000.0, eps_ttm=6.0, dps_ttm=0.92,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )

    # Full XBRL summary — all five ratio fields present.  ``peg`` is always
    # surfaced as ``None`` by ``_load_xbrl_summary`` (there is no PIT-correct
    # source for the forward-growth term — see the provider docstring).
    fake_xbrl: dict = {
        "profit_margin":      0.25,
        "debt_to_equity":     1.5,
        "roe":                0.15,
        "revenue_growth_yoy": 0.07,
        "free_cash_flow":     9.0e10,
        "peg":                None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: fake_xbrl)

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 10, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)

    # Core XBRL-derived ratios.
    assert out.profit_margin      == pytest.approx(0.25)
    assert out.debt_to_equity     == pytest.approx(1.5)
    assert out.roe                == pytest.approx(0.15)
    assert out.revenue_growth_yoy == pytest.approx(0.07)
    assert out.free_cash_flow     == pytest.approx(9.0e10)
    # PEG is intentionally always None — no PIT-correct source available.
    assert out.peg                is None

    # as_of must be populated.
    from datetime import date
    assert out.as_of == date(2023, 3, 10)


@pytest.mark.asyncio
async def test_pit_composite_all_xbrl_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """When XBRL summary returns all None, every new ratio field is None — no exception raised."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Stub Co", sector="Industrials",
        shares_out=1_000_000.0, eps_ttm=2.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(10, last_close=50.0)),
    )

    # Empty XBRL summary — all ratios absent (e.g. ADR with no EDGAR data).
    empty_xbrl: dict = {
        "profit_margin":      None,
        "debt_to_equity":     None,
        "roe":                None,
        "revenue_growth_yoy": None,
        "free_cash_flow":     None,
        "peg":                None,
        "_peg_source":        None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: empty_xbrl)

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 10, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.profit_margin      is None
    assert out.debt_to_equity     is None
    assert out.roe                is None
    assert out.revenue_growth_yoy is None
    assert out.free_cash_flow     is None
    assert out.peg                is None

    # Price-derived fields must still be present (from yfinance branch).
    assert out.last_price == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_pit_composite_partial_xbrl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Partial XBRL data — some fields populated, others None — is handled gracefully."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Partial Co", sector="Healthcare",
        shares_out=500_000_000.0, eps_ttm=3.5, dps_ttm=0.5,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(60, last_close=120.0)),
    )

    # Only profit_margin and roe available; the rest are missing concepts.
    partial_xbrl: dict = {
        "profit_margin":      0.18,
        "debt_to_equity":     None,   # StockholdersEquity concept missing
        "roe":                0.12,
        "revenue_growth_yoy": None,   # Prior-year Revenues missing
        "free_cash_flow":     None,   # CapEx concept missing
        "peg":                None,
        "_peg_source":        None,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary", lambda *a, **k: partial_xbrl)

    out = await mod.fetch("HLTH", as_of=datetime(2023, 6, 15, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)

    # Populated fields must be correct.
    assert out.profit_margin == pytest.approx(0.18)
    assert out.roe           == pytest.approx(0.12)

    # Missing fields must be None, not zero or a default.
    assert out.debt_to_equity     is None
    assert out.revenue_growth_yoy is None
    assert out.free_cash_flow     is None
    assert out.peg                is None

    # as_of must be set.
    from datetime import date
    assert out.as_of == date(2023, 6, 15)
