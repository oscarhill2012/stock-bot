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

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert out.long_name  == "X Co"
    assert out.last_price is None
    assert out.market_cap is None


def test_pit_composite_registers_on_import() -> None:
    import data.providers.company_ratios.pit_composite  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("company_ratios", "pit_composite")]
    assert entry.upstream == "yfinance"   # shares yfinance limiter for price data
