"""yfinance providers — ``as_of`` parity + 52-week extremes + analyst counters."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from data.models import CompanyRatios, PriceHistory


@pytest.mark.asyncio
async def test_fetch_price_history_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_price_history`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_price_history",
        lambda ticker, period, interval: PriceHistory(ticker=ticker, bars=[]),
    )

    out = await mod.fetch_price_history(
        "AAPL",
        period="1y",
        interval="1d",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )

    assert isinstance(out, PriceHistory)
    assert out.ticker == "AAPL"


@pytest.mark.asyncio
async def test_fetch_company_ratios_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_company_ratios`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_company_ratios",
        lambda ticker, period, interval, as_of=None: CompanyRatios(ticker=ticker),
    )

    out = await mod.fetch_company_ratios(
        "AAPL",
        period="1y",
        interval="1d",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )

    assert isinstance(out, CompanyRatios)
    assert out.ticker == "AAPL"


@pytest.mark.asyncio
async def test_stats_yfinance_surfaces_52w_and_analyst_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that the four new fields (52-week extremes + analyst counters) are
    populated from yfinance ``info`` and that ``as_of`` is stamped onto the result.

    ``_fetch_info_dict`` is monkeypatched so no real yfinance network call is made.
    ``_fetch_company_ratios`` is called via the public async wrapper to exercise
    the full dispatch path.
    """
    import data.providers.stats.yfinance as mod

    fake_info = {
        "fiftyDayAverage": 170.0,
        "twoHundredDayAverage": 150.0,
        "fiftyTwoWeekHigh": 180.0,
        "fiftyTwoWeekLow": 120.0,
        "recommendationMean": 2.1,
        "numberOfAnalystOpinions": 42,
        "beta": 1.2,
        "marketCap": 2.7e12,
    }

    # Patch _fetch_info_dict so the ratios builder gets our fake payload.
    monkeypatch.setattr(mod, "_fetch_info_dict", lambda *a, **kw: fake_info)

    # Also patch _yt_raw to avoid a real yfinance call (fast dict lookup).
    monkeypatch.setattr(
        mod, "_yt_raw",
        lambda *a, **kw: {"history": None, "info": fake_info, "fast": {}},
    )

    as_of_date = date(2023, 3, 10)
    ratios = await mod.fetch_company_ratios(
        "AAPL",
        as_of=as_of_date,
    )

    # --- 52-week extremes ---
    assert ratios.fifty_two_week_high == 180.0
    assert ratios.fifty_two_week_low == 120.0

    # --- analyst counters ---
    assert ratios.analyst_rating_avg == 2.1
    assert ratios.number_of_analyst_opinions == 42

    # --- as_of stamped onto the result ---
    assert ratios.as_of == as_of_date


@pytest.mark.asyncio
async def test_stats_yfinance_as_of_datetime_is_coerced_to_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``as_of`` is a ``datetime``, it should be coerced to a plain ``date``
    before being stored on ``CompanyRatios.as_of``."""
    import data.providers.stats.yfinance as mod

    fake_info: dict = {}

    monkeypatch.setattr(mod, "_fetch_info_dict", lambda *a, **kw: fake_info)
    monkeypatch.setattr(
        mod, "_yt_raw",
        lambda *a, **kw: {"history": None, "info": fake_info, "fast": {}},
    )

    as_of_dt = datetime(2023, 3, 10, 14, 30, tzinfo=UTC)
    ratios = await mod.fetch_company_ratios("AAPL", as_of=as_of_dt)

    assert ratios.as_of == date(2023, 3, 10)


@pytest.mark.asyncio
async def test_stats_yfinance_missing_fields_are_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When yfinance returns no 52-week or analyst keys, the fields are ``None``
    rather than raising or returning a fallback value."""
    import data.providers.stats.yfinance as mod

    # Info dict deliberately omits the four new keys.
    fake_info = {"beta": 1.1, "marketCap": 1.0e12}

    monkeypatch.setattr(mod, "_fetch_info_dict", lambda *a, **kw: fake_info)
    monkeypatch.setattr(
        mod, "_yt_raw",
        lambda *a, **kw: {"history": None, "info": fake_info, "fast": {}},
    )

    ratios = await mod.fetch_company_ratios("AAPL", as_of=date(2023, 3, 10))

    assert ratios.fifty_two_week_high is None
    assert ratios.fifty_two_week_low is None
    assert ratios.analyst_rating_avg is None
    assert ratios.number_of_analyst_opinions is None
