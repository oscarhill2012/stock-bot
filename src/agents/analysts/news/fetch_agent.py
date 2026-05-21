"""NewsFetchAgent — BaseAgent that fetches news for every watchlist ticker.

Phase 9 introduced the per-ticker fan-out design: each ``NewsAnalyst_<TICKER>``
reads only its own ticker's context.  This agent writes one
``temp:news_context_<TICKER>`` key per ticker so ADK's
``inject_session_state`` fills each branch's ``{news_context}``
placeholder with single-ticker text.

The legacy ``news_fetch_callback`` (a ``before_agent_callback`` on the batched
``NewsAnalyst`` LlmAgent) was retired when this agent landed — see
``agents.analysts.news.fetch`` for the retained formatting helpers.

Yielded keys (one state_delta event):
  - ``temp:news_data``  — dict[ticker, {"news": [serialised NewsArticle, ...]}]
  - ``temp:news_context_<TICKER>`` — formatted text block for one ticker
  - ``temp:news_context`` — aggregate joined block (all tickers); retained for
    trace/debug surfaces per Phase 9 spec §1
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

# Reuse the per-ticker formatter from the shared helpers module.
# This avoids duplicating the article-truncation and formatting logic.
from agents.analysts.news.fetch import _build_ticker_news_context
from data import get_stock_news
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe

_LOGGER = logging.getLogger(__name__)


class NewsFetchAgent(BaseAgent):
    """Fetch news for every watchlist ticker; yield per-ticker context keys.

    Reads ``state["tickers"]`` and ``state["as_of"]``; writes
    ``temp:news_data`` and one ``temp:news_context_<TICKER>`` per ticker
    via a single ``state_delta`` event.

    The agent is idempotent for a given ``(tickers, as_of)`` input — re-
    running yields identical keys (subject to provider determinism).
    """

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fetch news for all watchlist tickers and yield a single state_delta event.

        Iterates over ``state["tickers"]``, calling the news provider for each
        one.  Provider failures are caught per-ticker so a single bad provider
        call degrades gracefully — that ticker gets an empty news list and an
        ``(no news available)`` placeholder in its context block.

        Args:
            ctx: ADK invocation context carrying the session state.

        Yields:
            One ``Event`` whose ``actions.state_delta`` carries:
              - ``temp:news_data`` — machine-readable dict keyed by ticker
              - ``temp:news_context_<TICKER>`` — formatted single-ticker block
              - ``temp:news_context`` — aggregate multi-ticker block for traces
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []

        # Resolve the historical clock (backtest) or wall-clock (live).
        as_of: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="news/fetch_agent",
        )

        news_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []

            # Serialise so downstream consumers always see plain dicts,
            # regardless of whether the provider returned Pydantic models.
            serialised = [
                a.model_dump() if hasattr(a, "model_dump") else a for a in articles
            ]
            news_data[ticker] = {"news": serialised}

            # Build the per-ticker formatted block.  ADK's instruction template
            # will fill this into ``{news_context}`` for the ticker's LlmAgent.
            per_ticker_blocks[ticker] = _build_ticker_news_context(ticker, serialised)

        # Build the state_delta payload.  All keys are temp:-prefixed
        # (Rule 2) so ADK strips them at the invocation boundary.
        delta: dict[str, object] = {"temp:news_data": news_data}

        # Write one per-ticker key — each ticker's LlmAgent reads ONLY its own.
        for ticker, block in per_ticker_blocks.items():
            delta[f"temp:news_context_{ticker}"] = block

        # Retain the aggregate ``temp:news_context`` key — the multi-ticker
        # joined block — for trace/debug surfaces (Phase 9 spec §1).
        # Each per-ticker LlmAgent reads its own ``temp:news_context_<TICKER>``;
        # this key is only for human-readable traces.
        delta["temp:news_context"] = "\n\n".join(
            f"=== {t} ===\n{per_ticker_blocks[t]}" for t in tickers
        )

        # Surface trace — no-op unless state["_trace"] is set.
        _trace_maybe(state, "01_fetch_news", news_data)

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta=delta),
        )
