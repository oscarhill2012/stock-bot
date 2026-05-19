"""Contract test: generate_ticks reads session times from pandas_market_calendars.

These tests assert that ``schedule.generate_ticks`` is calendar-driven, not
config-driven.  The key proof is the early-close day: if the implementation
still hardcodes 16:00 ET the close tick will disagree with the 13:00 ET the
NYSE calendar reports for day-after-Thanksgiving.
"""
from __future__ import annotations

from datetime import date

from backtest.settings import BacktestSettings


def _make_settings(*, ticks_per_day: list[str]) -> BacktestSettings:
    """Build a sandboxed ``BacktestSettings`` object for injection via monkeypatch.

    Parameters
    ----------
    ticks_per_day:
        The phase list to embed in the settings.

    Returns
    -------
    BacktestSettings
        A fully-constructed settings object with neutral values for all
        non-schedule fields.
    """
    return BacktestSettings(
        backtests_root               = "x",
        ticks_per_day                = ticks_per_day,
        failed_tick_abort_ratio      = 0.1,
        fake_broker_starting_cash    = 100.0,
        forward_return_horizons_days = [1],
        ohlcv_warmup_days            = 30,
    )


def test_early_close_day_yields_thirteen_hundred_close(monkeypatch) -> None:
    """Day-after-Thanksgiving 2024 NYSE close is 13:00 ET — proves the calendar is the source of truth.

    If ``schedule.py`` were hardcoding 16:00 ET this would fail, confirming
    that ``pandas_market_calendars`` (not config) owns session times.
    """
    from backtest import schedule
    from backtest import settings as bs_mod

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["open", "close"]))

    ticks = schedule.generate_ticks(date(2024, 11, 29), date(2024, 11, 29))
    close_tick = next(t for t in ticks if t.phase == "close")

    from zoneinfo import ZoneInfo

    ny_close = close_tick.as_of.astimezone(ZoneInfo("America/New_York"))
    assert ny_close.hour   == 13, (
        f"expected 13:00 ET early close, got {ny_close.hour:02d}:{ny_close.minute:02d}"
    )
    assert ny_close.minute == 0


def test_ticks_per_day_open_only_halves_tick_count(monkeypatch) -> None:
    """Flipping ticks_per_day to just ``['open']`` halves the count vs ``['open', 'close']``.

    Confirms that ``ticks_per_day`` is the sole policy knob for phase
    selection — and that the calendar-based path still honours it correctly.
    """
    from backtest import schedule
    from backtest import settings as bs_mod

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["open", "close"]))
    full = schedule.generate_ticks(date(2024, 1, 2), date(2024, 1, 12))

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["open"]))
    open_only = schedule.generate_ticks(date(2024, 1, 2), date(2024, 1, 12))

    assert len(open_only) * 2 == len(full)
    assert all(t.phase == "open" for t in open_only)


def test_unsupported_phase_raises(monkeypatch) -> None:
    """A typo or unimplemented phase in ``ticks_per_day`` raises ``ValueError``.

    Ensures that an invalid config value surfaces loudly rather than silently
    producing zero ticks or incorrect output.
    """
    import pytest

    from backtest import schedule
    from backtest import settings as bs_mod

    monkeypatch.setattr(bs_mod, "_cache", _make_settings(ticks_per_day=["opening", "close"]))

    with pytest.raises(ValueError, match="unsupported ticks_per_day"):
        schedule.generate_ticks(date(2024, 1, 2), date(2024, 1, 2))
