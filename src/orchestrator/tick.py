"""One-shot tick entrypoint. Runs once per Cloud Run Job invocation."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime
from enum import Enum

logger = logging.getLogger(__name__)


class BrokerMode(Enum):
    """Enumeration of supported broker operating modes.

    Used to partition ADK session state between paper and live runs —
    each mode maps to a distinct ``app_name`` so their ``user_state``
    rows are structurally disjoint in the ``DatabaseSessionService``.
    """

    LIVE  = "live"
    PAPER = "paper"


def _dispatch_app_name(broker_mode: BrokerMode) -> str:
    """Return the ADK app_name for the current broker mode.

    Parameters
    ----------
    broker_mode
        ``BrokerMode.LIVE`` or ``BrokerMode.PAPER`` — read from the
        broker layer configuration.

    Returns
    -------
    str
        ``"StockBot-live"`` or ``"StockBot-paper"``.  These values
        partition the ADK user_state table so paper and live portfolios
        cannot share thesis rows.  Backtest uses a third value,
        ``f"StockBot-backtest-{window_key}"``, set in the backtest
        driver / runner — tick.py does not handle that path.

    Raises
    ------
    ValueError
        When ``broker_mode`` is not one of the supported enum members.
    """
    match broker_mode:
        case BrokerMode.LIVE:
            return "StockBot-live"
        case BrokerMode.PAPER:
            return "StockBot-paper"
        case _:
            raise ValueError(f"Unsupported broker mode: {broker_mode!r}")

# Symbols fetched once per tick as market and sector benchmarks.
# SPY is the broad-market reference; the 11 SPDR sector ETFs cover every
# S&P 500 constituent sector.  These are fetched in a single bulk yfinance
# call (one round-trip) and stored under ``state["reference_prices"]`` so
# the technical extractor can compute relative-strength features without
# issuing any additional network calls.
_REFERENCE_SYMBOLS: tuple[str, ...] = (
    "SPY",                                            # broad market benchmark
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",       # SPDR sector ETFs (batch 1)
    "XLI", "XLB", "XLRE", "XLU", "XLC",              # SPDR sector ETFs (batch 2)
)


async def _fetch_reference_prices(
    symbols: tuple[str, ...],
    *,
    as_of: date,
    period: str = "1y",
    interval: str = "1d",
) -> dict:
    """Fetch SPY + 11 SPDR sector ETFs in one bulk yfinance call.

    Delegates to ``_bulk_download`` in the yfinance stats provider, which
    issues a single ``yf.download`` round-trip rather than 12 sequential
    per-ticker calls.  This keeps the per-tick yfinance budget low and
    avoids queueing delays in the analyst pool.

    Parameters
    ----------
    symbols:
        Tuple of ticker symbols to fetch (default: ``_REFERENCE_SYMBOLS``).
    as_of:
        Point-in-time date — forwarded to the bulk downloader for interface
        parity; yfinance uses wall-clock anchored periods internally.
    period:
        yfinance history period string (default ``"1y"``).
    interval:
        yfinance history interval string (default ``"1d"``).

    Returns
    -------
    dict[str, PriceHistory]
        One ``PriceHistory`` per requested symbol, keyed by symbol string.
    """
    from data.providers.stats.yfinance import _bulk_download

    return await _bulk_download(symbols, period=period, interval=interval, as_of=as_of)


async def _build_initial_state(broker, tick_id: str, tickers: list[str]) -> dict:
    """Build the initial pipeline state for one live tick.

    Reads the live portfolio from the broker, fetches reference prices,
    and seeds the Phase 2 lifecycle keys (``tick_id``, ``as_of``,
    ``tick_phase``) plus the per-tick pipeline fields the pipeline expects.

    Cross-tick fields (``user:positions``, ``user:thesis``) are NOT seeded
    here — ADK's user_state merge populates them from the
    ``DatabaseSessionService`` row when the session is created.  See
    ``docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md``
    (Spec B) for the full persistence model.

    Args:
        broker: Any broker implementing ``get_portfolio() -> Portfolio``.
        tick_id: The unique identifier string for this tick.
        tickers: The list of watchlist ticker symbols for this tick.

    Returns:
        A dict containing all keys the pipeline expects at startup,
        including a JSON-serialisable portfolio snapshot under
        ``"portfolio"`` and a wall-clock UTC ``as_of`` datetime under
        ``"as_of"`` (tick_phase is the literal string ``"live"``).
    """
    portfolio = await broker.get_portfolio()

    # Fetch SPY + sector ETF price histories in one bulk call so the technical
    # extractor can compute relative-strength features without issuing any
    # additional network calls during the analyst pool phase.
    reference_prices = await _fetch_reference_prices(
        _REFERENCE_SYMBOLS, as_of=date.today(),
    )

    return {
        "tick_id": tick_id,
        # Phase 2 lifecycle handshake — the live builder is the single
        # authoritative writer of ``as_of`` and ``tick_phase``.  Backtest
        # sets the equivalents in ``src/backtest/driver.py``.  These
        # keys are documented in ``docs/contract-invariants.md`` §A.
        #
        # ISO-stringified at the state boundary — DatabaseSessionService
        # JSON-serialises state and cannot persist raw datetime objects.
        # Parity invariant: backtest writes the same shape; every consumer
        # reads via ``data.timeguard.resolve_as_of`` which round-trips the
        # string back to a tz-aware ``datetime``.  See plan 04.
        #
        # ``STOCKBOT_STRICT_AS_OF=1`` is set by backtest runs to veto the
        # wall-clock fallback in ``resolve_as_of``; live must NOT set that
        # env var — this seeded ISO instant is the wall-clock truth for the
        # tick, so the fallback is never reached anyway.
        "as_of":      datetime.now(tz=UTC).isoformat(),
        "tick_phase": "live",
        "tickers": tickers,
        "memory_buffer": [],
        "day_digest": "",
        "portfolio": portfolio.model_dump(mode="json"),
        # Dump each PriceHistory to a JSON-safe dict so the ADK SqlSessionService
        # (which serialises state via plain json.dumps) doesn't choke on Pydantic
        # objects.  The technical extractor coerces dicts back to PriceHistory
        # on the consumer side — see src/contract/extractors/technical.py.
        "reference_prices": {
            sym: ph.model_dump(mode="json") for sym, ph in reference_prices.items()
        },
    }


async def run_once(broker, session=None, *, tick_label: str | None = None) -> dict:
    """Run one hourly tick and return the final session state dict.

    Creates a fresh ADK session, seeds the initial tick state, runs the
    full pipeline, then reads back the completed session state.

    Parameters
    ----------
    broker:
        Any broker implementing ``get_portfolio() -> Portfolio``.
    session:
        Optional pre-built ADK session (used in tests).
    tick_label:
        Optional human-readable label for the tick, e.g. ``"1/3"``.  When
        provided, the terminal-log banner reads ``Tick {label}``.  When
        ``None``, the banner reads ``Tick``.
    """
    import time as _time

    from google.genai import types as genai_types

    from observability.terminal_log import _TICK_LOGGER
    from orchestrator.persistence import make_session_service
    from orchestrator.pipeline import build_pipeline
    from orchestrator.stock_picker import get_watchlist

    tick_id = (
        f"tick-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S')}"
        f"-{uuid.uuid4().hex[:8]}"
    )
    tickers = get_watchlist()

    # ── Terminal-log banner ─────────────────────────────────────────────────
    # Emitted unconditionally — the tick logger is always present.  The
    # human-readable terminal handler only attaches when
    # ``setup_terminal_logging()`` was called (i.e. in smoke_run.py); on
    # other paths the records fall through to the root handler as normal.
    # ``logging`` is already imported at module level above.
    _tick_log = logging.getLogger(_TICK_LOGGER)

    _banner_label = f"Tick {tick_label}" if tick_label else "Tick"
    _wall_time_str = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    _sep = "═" * 67
    _tick_log.info(_sep)
    _tick_log.info(
        f"  {_banner_label}  ·  {_wall_time_str}  ·  {len(tickers)} tickers"
    )
    _tick_log.info(_sep)

    _tick_start = _time.perf_counter()

    # Phase 9: pass the current watchlist so the News and Fundamental analyst
    # branches are built with per-ticker fan-out for exactly these tickers.
    # ``tickers`` was resolved above via ``get_watchlist()`` and is also
    # seeded into the ADK session state by ``_build_initial_state`` below.
    pipeline = build_pipeline(broker, session, tickers=tickers)

    # Resolve the broker mode from its ``mode`` attribute (``"paper"`` or
    # ``"live"``).  FakeBroker does not expose ``.mode``; default to PAPER
    # so test runs land in the paper namespace rather than raising.
    _raw_mode = getattr(broker, "mode", "paper")
    _broker_mode = BrokerMode(_raw_mode) if _raw_mode in BrokerMode._value2member_map_ else BrokerMode.PAPER
    _app_name = _dispatch_app_name(_broker_mode)

    from orchestrator.lifecycle_runner import build_runner, build_seed_state

    session_service = make_session_service()

    # Parity: build the runner through the shared helper so the
    # HandleInjectorPlugin is always installed on the same code path
    # the backtest driver uses.  Live currently has no TraceWriter or
    # DecisionLogger wired in (both default to None) — the plugin
    # registers as a structural no-op, but the install pathway is
    # symmetric with the backtest driver so future handle wiring lands
    # in exactly one place.
    runner = build_runner(
        agent           = pipeline,
        app_name        = _app_name,
        session_service = session_service,
        trace_writer    = None,
        decision_logger = None,
    )

    # Create a fresh session with the minimal state every tick needs.
    # Portfolio is seeded from the broker so the strategist's held-view
    # callback renders real holdings on the very first tick.
    # Cross-tick state (user:positions, user:thesis) is NOT seeded here —
    # ADK's user_state merge hydrates it from the DB row on session create
    # (Spec B: docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md).
    initial_state = await _build_initial_state(broker, tick_id, tickers)
    # build_seed_state strips temp: keys and ISO-coerces datetimes —
    # parity with backtest.driver.Driver.run_tick.
    adk_session = await session_service.create_session(
        app_name = _app_name,
        user_id  = "stockbot",
        state    = build_seed_state(initial_state),
    )

    events = runner.run_async(
        user_id="stockbot",
        session_id=adk_session.id,
        new_message=genai_types.Content(
            parts=[genai_types.Part(text=f"Run tick {tick_id}")],
            role="user",
        ),
    )
    try:
        async for _ in events:
            pass
    except (AttributeError, BaseException) as exc:
        # ADK 1.32 has a known runner-cleanup bug: after the pipeline runs, the
        # runner may raise AttributeError('NoneType'.partial) or a
        # BaseExceptionGroup wrapping GeneratorExit from parallel-agent teardown.
        # Both happen *after* session state has been written, so the tick result
        # is still available via session_service.get_session(). We log and
        # continue; the caller reads state from the session service below.
        logger.warning(
            "ADK runner raised during tick %s (pipeline likely completed): %s: %s",
            tick_id, type(exc).__name__, exc,
        )

    updated = await session_service.get_session(
        app_name=_app_name,
        user_id="stockbot",
        session_id=adk_session.id,
    )

    # ── Tick summary ────────────────────────────────────────────────────────
    _tick_elapsed = _time.perf_counter() - _tick_start
    _executions   = updated.state.get("executions", []) if isinstance(updated.state, dict) else []
    _tick_log.info(
        f"\n  {_banner_label} done in {_tick_elapsed:.1f}s"
        f"  ·  executions: {len(_executions)}"
    )

    return updated.state


def main():
    """CLI entry point for running one tick against the real Trading 212 broker."""
    import argparse
    import os

    import httpx

    from broker.trading212 import Trading212Broker

    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="paper", choices=["paper", "live"])
    args = parser.parse_args()

    broker = Trading212Broker(
        mode=args.mode,
        api_key=os.environ["TRADING212_API_KEY"],
        http_client=httpx.AsyncClient(),
        instrument_map={},
    )
    asyncio.run(run_once(broker))


if __name__ == "__main__":
    main()
