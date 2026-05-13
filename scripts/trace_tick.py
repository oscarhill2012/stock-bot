"""Single-ticker surface-trace entrypoint.

Usage::

    PYTHONPATH=src python -m scripts.trace_tick --ticker AAPL [--out docs/surface-traces/]

Runs one full hourly tick with the production pipeline against the real LLM,
paper broker, against a single ticker.  Captures a labelled JSON trace at
every pipeline boundary and writes it to disk.

The script mirrors the bootstrapping logic in ``scripts/smoke_run.py`` and
``orchestrator/tick.py``, with one key difference: it seeds ``state["_trace"]``
with a ``TraceWriter`` instance so every ``_trace_maybe(...)`` hook in the
pipeline writes a section.  On production ticks (no ``_trace`` key) all hooks
are zero-cost no-ops.

Entrypoint adaptation notes (compared to the plan's template):
- ``run_once(broker)`` in ``orchestrator/tick.py`` builds its own pipeline and
  initial state internally.  We cannot inject ``_trace`` through that API
  without touching production code.  Instead, this script inlines the ADK
  session setup (mirroring ``run_once``) so ``_trace`` is seeded into the
  initial session state before the runner starts.
- ``FakeBroker`` is used (matching ``smoke_run.py``) rather than
  ``Trading212Broker`` — the trace harness is designed for local surface
  validation, not a paper-account live run.  To run against Trading212, set
  the ``--broker real`` flag.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


async def main_async(argv: list[str] | None = None) -> int:
    """Parse arguments, bootstrap the pipeline, run one traced tick, write JSON.

    Parameters
    ----------
    argv:
        Argument list (defaults to ``sys.argv[1:]`` when ``None``).

    Returns
    -------
    int
        Exit code — 0 on success, 1 on failure (partial trace written).
    """
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ticker", required=True, help="Ticker symbol to trace (e.g. AAPL).")
    p.add_argument(
        "--out",
        default="docs/surface-traces",
        help="Directory to write trace JSON files into.",
    )
    p.add_argument(
        "--broker",
        choices=["fake", "real"],
        default="fake",
        help="'fake' uses FakeBroker (default); 'real' uses Trading212 paper account.",
    )
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Mark the process as trace mode so LLM-side callbacks attach themselves
    # to the Fundamental, News, and Strategist agents.
    os.environ["STOCKBOT_TRACE"] = "1"

    # ── Lazy imports after env var is set so LLM agents see STOCKBOT_TRACE ──
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    from observability.trace import TraceWriter
    from orchestrator.pipeline import build_pipeline

    # ── Broker selection ──────────────────────────────────────────────────────
    if args.broker == "real":
        import httpx  # noqa: PLC0415

        from broker.trading212 import Trading212Broker  # noqa: PLC0415
        broker = Trading212Broker(
            mode="paper",
            api_key=os.environ["TRADING212_API_KEY"],
            http_client=httpx.AsyncClient(),
            instrument_map={},
        )
    else:
        import yfinance as yf  # noqa: PLC0415

        from broker.fake import FakeBroker  # noqa: PLC0415
        # Seed with a single-ticker price so FakeBroker can compute weights.
        ticker_upper = args.ticker.upper()
        try:
            h = yf.Ticker(ticker_upper).history(period="1d")
            price = float(h["Close"].iloc[-1]) if not h.empty else 100.0
        except Exception:
            price = 100.0
        broker = FakeBroker(starting_cash=10_000.0, prices={ticker_upper: price})

    # ── Pipeline + session setup ──────────────────────────────────────────────
    tick_id = (
        f"trace-{datetime.now(tz=UTC).strftime('%Y%m%dT%H%M%S')}"
        f"-{uuid.uuid4().hex[:8]}"
    )

    pipeline = build_pipeline(broker)
    # In-memory session — the trace harness is a single-shot local debug run,
    # so we deliberately avoid the DB-backed session service. That matters
    # because the production DatabaseSessionService JSON-serialises state on
    # every flush, and ``state["_trace"]`` holds a TraceWriter (not JSON-safe).
    session_service = InMemorySessionService()
    runner = Runner(
        agent=pipeline,
        app_name="StockBot",
        session_service=session_service,
    )

    # Build the initial state, mirroring orchestrator/tick.py _build_initial_state,
    # but scoped to the single requested ticker and with _trace injected.
    tw = TraceWriter()
    portfolio = await broker.get_portfolio()
    initial_state = {
        "tick_id":       tick_id,
        "tickers":       [args.ticker.upper()],
        "memory_buffer": [],
        "day_digest":    "",
        "thesis":        "",
        "positions":     {},
        "portfolio":     portfolio.model_dump(mode="json"),
        # Surface trace hook — every _trace_maybe(state, ...) call will route
        # through this writer.  Absent on production ticks.
        "_trace":        tw,
    }

    adk_session = await session_service.create_session(
        app_name="StockBot",
        user_id="stockbot",
        state=initial_state,
    )

    # ── Run one tick ──────────────────────────────────────────────────────────
    try:
        events = runner.run_async(
            user_id="stockbot",
            session_id=adk_session.id,
            new_message=genai_types.Content(
                parts=[genai_types.Part(text=f"Run tick {tick_id}")],
                role="user",
            ),
        )
        async for _ in events:
            pass

    except (AttributeError, BaseException) as exc:
        # ADK 1.32+ known runner-cleanup quirk — pipeline may have completed
        # successfully even when the runner raises on teardown.  Flush a partial
        # trace and surface the error to the caller.
        path = out_dir / f"{tick_id}-{args.ticker.upper()}-PARTIAL.json"
        tw.finalise(path)
        print(
            f"✗ trace tick failed; partial written to {path}",
            file=sys.stderr,
        )
        import traceback as _tb
        _tb.print_exc()
        # Drill into ExceptionGroup sub-exceptions — TaskGroup-wrapped errors
        # in parallel agents would otherwise be hidden behind a generic
        # "unhandled errors in a TaskGroup" message.
        if hasattr(exc, "exceptions"):
            for i, sub in enumerate(exc.exceptions):
                print(f"  sub-exception [{i}]: {type(sub).__name__}: {sub}", file=sys.stderr)
                _tb.print_exception(sub)
        print(
            f"  cause: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    # ── Flush the completed trace ─────────────────────────────────────────────
    path = out_dir / f"{tick_id}-{args.ticker.upper()}.json"
    tw.finalise(path)
    print(f"✓ trace written to {path}")
    return 0


def main() -> int:
    """Synchronous entry point — wraps ``main_async`` in ``asyncio.run``."""
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
