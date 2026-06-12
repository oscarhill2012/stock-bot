"""initialise — pre-flight, anchor snapshot, scheduler resume."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, text

from lifecycle._tables import STOCKBOT_TABLES as _STOCKBOT_TABLES
from orchestrator.persistence import (
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)

from . import scheduler

_REQUIRED_ENV = ("TRADING212_API_KEY", "FINNHUB_API_KEY")


class NonEmptyTablesError(RuntimeError):
    pass


class EnvVarMissingError(RuntimeError):
    pass


class BrokerCashMismatch(RuntimeError):
    pass


class UnsupportedSchemaError(RuntimeError):
    """Raised when a Postgres URL targets a schema other than 'public'.

    The lifecycle helpers use un-qualified table references (e.g. ``SELECT
    COUNT(*) FROM trades``), which Postgres resolves via ``search_path``.
    When ``search_path`` defaults to ``public`` this is correct.  If a URL
    pins a *different* schema the queries would silently hit the wrong tables
    (or find nothing and report a false-positive empty state).

    Multi-schema deployment is a portability concern, not a correctness
    one at this stage.  If multi-schema support is needed in future:

    1. Add a ``schema: str = "public"`` argument to every lifecycle helper.
    2. Qualify every table reference: ``f"{schema}.{table}"`` (there is one
       existing explicit ``public.`` qualifier in ``hard_reset.py`` around
       line 68 that would also need parameterising).
    3. Remove this guard once all sites are schema-aware.
    """


@dataclass(frozen=True)
class InitResult:
    anchor_tick_id: str
    anchor_bot_value: float
    anchor_spy_price: float
    scheduler_job: str | None


def _fetch_spy_price() -> float:
    """Get the latest SPY close. Pulled out as a function for monkey-patching."""
    import yfinance as yf
    t = yf.Ticker("SPY")
    hist = t.history(period="1d")
    if hist.empty:
        raise RuntimeError("yfinance returned no SPY data")
    return float(hist["Close"].iloc[-1])


def _check_env() -> None:
    missing = [v for v in _REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise EnvVarMissingError(f"missing required env vars: {missing}")


def _check_heuristics() -> None:
    """Fail-fast load of analyst heuristics. Surfaces JSON/schema errors at boot.

    Imports the loader inside the function so the lifecycle module does not pull
    the agents package at import time (avoiding circular-import risk). If the
    JSON file is missing, malformed, or fails Pydantic validation this will raise
    immediately — before any ticker work begins.
    """
    # Deferred import so lifecycle does not depend on agents at module level.
    from agents.analysts.heuristics import load_heuristics

    load_heuristics()  # raises ValidationError if malformed


def _check_live_tables_empty(db_url: str) -> None:
    """Verify the four StockBot tables are empty before initialisation.

    Connects to the database at ``db_url``, inspects each table listed in
    ``_STOCKBOT_TABLES``, and raises ``NonEmptyTablesError`` for any table
    that already contains rows.

    Assumes the default Postgres schema (``public``).  If the URL pins a
    different ``search_path``, the un-qualified ``SELECT COUNT(*)`` queries
    would silently hit the wrong schema.  This function detects that case
    and raises ``UnsupportedSchemaError`` — see that class for the migration
    path.

    Args:
        db_url: SQLAlchemy-compatible connection string.

    Raises:
        UnsupportedSchemaError: when the URL contains an explicit
            ``search_path`` that is not ``public``.
        NonEmptyTablesError: when any monitored table already has rows.
    """
    # Guard: scope to the query string only, so a hostname or database name
    # that happens to contain the substring "search_path" does not trigger
    # a false positive.  Only the query portion (after '?') carries URL
    # parameters.
    query = db_url.split("?", 1)[1] if "?" in db_url else ""

    # Reject only when search_path is present AND the value is not exactly
    # 'public' (plain or percent-encoded '='), terminated by a separator or
    # end-of-string.  Values like 'notpublic' or 'public_tenant' contain
    # "public" as a substring and must be rejected — hence the trailing anchor.
    if "search_path" in query and not re.search(
        r"search_path(?:=|%3D)public(?:[&,;\s]|$)",
        query,
        re.IGNORECASE,
    ):
        raise UnsupportedSchemaError(
            "non-default Postgres schema detected in db_url; only 'public' is "
            "supported by the lifecycle helpers.  See UnsupportedSchemaError "
            "docstring for the migration path."
        )

    engine = make_engine(db_url)
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    Session = make_session_factory(engine)
    s = Session()
    try:
        for t in _STOCKBOT_TABLES:
            if t in existing:
                count = s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
                if count > 0:
                    raise NonEmptyTablesError(
                        f"table {t} has {count} rows; run scripts.hard_reset first"
                    )
    finally:
        s.close()


async def _check_broker_cash(broker: Any, expected: float, tolerance: float = 1.0) -> None:
    portfolio = await broker.get_portfolio()
    actual = float(portfolio.cash)
    if abs(actual - expected) > tolerance:
        raise BrokerCashMismatch(
            f"broker cash {actual:.2f} differs from expected {expected:.2f} "
            f"by more than ${tolerance:.2f}; reset T212 cash and retry"
        )


def _write_anchor(db_url: str, *, starting_capital: float, spy_price: float) -> None:
    engine = make_engine(db_url)
    Session = make_session_factory(engine)
    s = Session()
    try:
        save_portfolio_snapshot(s, {
            "tick_id": "init",
            "recorded_at": datetime.now(tz=UTC),
            "bot_total_value": starting_capital,
            "bot_cash": starting_capital,
            "bot_positions_value": 0.0,
            "bot_position_count": 0,
            "spy_price": spy_price,
            "spy_value_if_held": starting_capital,
            "bot_return_pct": 0.0,
            "spy_return_pct": 0.0,
            "excess_return_pct": 0.0,
            "holdings_breakdown": {},
        })
        s.commit()
    finally:
        s.close()


async def initialise(
    *,
    db_url: str,
    starting_capital: float,
    broker_mode: str,
    watchlist: list[str],
    broker: Any,
    scheduler_job: str | None,
) -> InitResult:
    """Pre-flight, seed schema, write anchor, resume scheduler."""
    # 1. Env
    _check_env()

    # 1b. Analyst heuristics config — fail fast before any DB or broker work
    _check_heuristics()

    # 2. Schema seed (idempotent)
    create_all(make_engine(db_url))

    # 3. Live tables empty
    _check_live_tables_empty(db_url)

    # 4. Broker reachable + cash matches
    await _check_broker_cash(broker, starting_capital)

    # 5. SPY price for anchor
    spy_price = _fetch_spy_price()

    # 6. Write anchor snapshot
    _write_anchor(db_url, starting_capital=starting_capital, spy_price=spy_price)

    # 7. Resume scheduler
    if scheduler_job:
        scheduler.resume_job(scheduler_job)

    return InitResult(
        anchor_tick_id="init",
        anchor_bot_value=starting_capital,
        anchor_spy_price=spy_price,
        scheduler_job=scheduler_job,
    )
