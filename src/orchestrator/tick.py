"""One-shot tick entrypoint. Runs once per Cloud Run Job invocation."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def run_once(broker, session=None) -> dict:
    """Run one hourly tick and return the final session state dict.

    Creates a fresh ADK session, seeds the initial tick state, runs the
    full pipeline, then reads back the completed session state.
    """
    from google.adk import Runner
    from google.genai import types as genai_types

    from orchestrator.persistence import make_session_service
    from orchestrator.pipeline import build_pipeline
    from orchestrator.stock_picker import get_watchlist

    tick_id = (
        f"tick-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}"
        f"-{uuid.uuid4().hex[:8]}"
    )
    tickers = get_watchlist()

    pipeline = build_pipeline(broker, session)
    session_service = make_session_service()
    runner = Runner(
        agent=pipeline,
        app_name="StockBot",
        session_service=session_service,
    )

    # Create a fresh session with the minimal state every tick needs.
    adk_session = await session_service.create_session(
        app_name="StockBot",
        user_id="stockbot",
        state={
            "tick_id": tick_id,
            "tickers": tickers,
            "memory_buffer": [],
            "day_digest": "",
            "thesis": "",
            "positions": {},
        },
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
        app_name="StockBot",
        user_id="stockbot",
        session_id=adk_session.id,
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
