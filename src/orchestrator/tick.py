"""One-shot tick entrypoint. Runs once per Cloud Run Job invocation."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone


async def run_once(broker, session=None) -> dict:
    """Run one hourly tick and return summary."""
    from google.adk import Runner
    from google.adk.sessions import InMemorySessionService

    from orchestrator.pipeline import build_pipeline
    from orchestrator.stock_picker import get_watchlist

    tick_id = f"tick-{datetime.now(tz=timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    tickers = get_watchlist()

    pipeline = build_pipeline(broker, session)
    session_service = InMemorySessionService()
    runner = Runner(
        agent=pipeline,
        app_name="StockBot",
        session_service=session_service,
    )

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

    from google.genai import types as genai_types
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

    updated = await session_service.get_session(
        app_name="StockBot",
        user_id="stockbot",
        session_id=adk_session.id,
    )
    return updated.state


def main():
    import argparse
    from broker.trading212 import Trading212Broker
    import httpx, os

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
