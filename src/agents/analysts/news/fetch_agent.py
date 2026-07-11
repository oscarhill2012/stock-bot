"""NewsFetchAgent — fetch, route, staleness-filter, and render per ticker.

Phase 14 (Plan 3) rebuild.  Per tick this agent:

1. Fetches ``/company-news`` for every watchlist ticker (existing provider
   path — cache-backed during backtests; D1: no new providers).
2. Unions the feeds and de-duplicates exact re-fetches by ``article_key``
   (the same story is stapled onto several tickers' feeds — judge it once).
3. Routes the union through the specificity router (Plan 2):
   company-specific articles come back keyed by ticker in
   ``RoutedArticles.company``; roundup/macro articles land in ``.macro``,
   which this agent does NOT consume — Plan 5's macro analyst owns that
   stream.
4. Per ticker: title-level dedup (cheap exact/near-exact hygiene), then the
   deterministic embedding staleness pre-filter against the per-run
   ``NewsHistoryStore``.  Only novel articles render in full; previously
   seen articles render as headline-only drift context (D4).

Yielded state keys (one state_delta event):
    - ``temp:news_data`` — dict[ticker, {"news", "fresh", "stale"}] where
      ``news`` is the full routed set (deterministic-extractor input) and
      ``fresh``/``stale`` are the capped LLM-visible slices (report-cache
      key inputs).
    - ``temp:news_context_<TICKER>`` — the two-section context block.
    - ``temp:news_context`` — aggregate block (trace/debug only).

Failure policy: a per-ticker provider error degrades that ticker to an
empty feed (branch isolation), but an embedding failure RAISES — silently
mis-classifying staleness is the banned silent-degradation bug class.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts.news.fetch import (
    _build_ticker_news_context,
    _dedup_and_sort_articles,
    _freshest_first,
    article_key,
    partition_articles_by_staleness,
)
from agents.analysts.news.history import get_news_history_store
from agents.analysts.news.router import route_articles
from config.analysts import get_analysts_config
from data import get_stock_news
from data.timeguard import resolve_as_of
from observability.trace import trace_maybe
from orchestrator.stock_picker import get_watchlist_with_names

_LOGGER = logging.getLogger(__name__)


class NewsFetchAgent(BaseAgent):
    """Deterministic pre-LLM stage of the news branch (see module docstring)."""

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fetch, route, partition, and render news for every ticker.

        Parameters:
            ctx: the ADK invocation context carrying session state.

        Yields:
            One Event whose state_delta holds ``temp:news_data``, the
            per-ticker ``temp:news_context_<TICKER>`` blocks, and the
            aggregate ``temp:news_context``.
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []

        as_of: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="news/fetch_agent",
        )

        # Read caps/thresholds up front — the empty-watchlist short-circuit
        # below still needs nothing from here, but every other path does.
        cfg = get_analysts_config()

        # ── Empty-watchlist guard ────────────────────────────────────────
        # route_articles() raises ValueError on an empty watchlist (a loud
        # wiring guard for its normal callers); this agent's contract is to
        # degrade gracefully instead, so short-circuit before routing ever
        # runs.
        if not tickers:
            yield Event(
                author=self.name,
                invocation_id=ctx.invocation_id,
                actions=EventActions(state_delta={
                    "temp:news_data": {},
                    "temp:news_context": "",
                }),
            )
            return

        # ── 1. Fetch every ticker's feed ─────────────────────────────────
        all_articles: list = []
        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — per-ticker isolation
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []
            all_articles.extend(articles)

        # ── 2. Union-level identity dedup ────────────────────────────────
        # The same story appears on several tickers' /company-news feeds;
        # route each story once, not once per (story, feed) pair.
        unique: dict[str, object] = {}
        for article in all_articles:
            unique.setdefault(article_key(article), article)

        # ── 3. Specificity routing (Plan 2) ──────────────────────────────
        # ``routed.macro`` is deliberately ignored here — Plan 5 consumes it.
        # company_names is REQUIRED here: without it, matching degrades to
        # bare ticker symbols, which almost never appear in prose, and the
        # company streams would silently collapse to near-empty.
        company_names = {
            entry["symbol"]: entry["name"]
            for entry in get_watchlist_with_names()
        }
        routed = route_articles(
            list(unique.values()),
            tickers,
            company_names=company_names,
            roundup_threshold=cfg.news.roundup_company_threshold,
        )

        # ── 4. Per-ticker partition + render ─────────────────────────────
        store = get_news_history_store()
        threshold = cfg.staleness_similarity_threshold

        news_data: dict[str, dict] = {}
        context_blocks: dict[str, str] = {}

        for ticker in tickers:
            routed_articles = routed.company.get(ticker, []) or []

            # Serialise model objects so state stays JSON-safe end to end.
            serialised = [
                a.model_dump(mode="json") if hasattr(a, "model_dump") else a
                for a in routed_articles
            ]

            # Cheap title-level dedup first — collapse exact syndication
            # copies before any embedding is spent; the staleness filter
            # then catches the paraphrased rehashes this pass misses.
            deduped = _dedup_and_sort_articles(serialised)

            fresh, stale = await partition_articles_by_staleness(
                ticker, deduped, store=store, threshold=threshold,
            )

            # Apply the count caps HERE (freshest survive) so the rendered
            # block and the report-cache key hash byte-identical lists.
            fresh_capped = _freshest_first(fresh)[
                : cfg.news.max_articles_per_ticker
            ]
            stale_capped = _freshest_first(stale)[
                : cfg.news.max_stale_headlines_per_ticker
            ]

            news_data[ticker] = {
                "news": serialised,
                "fresh": fresh_capped,
                "stale": stale_capped,
            }
            context_blocks[ticker] = _build_ticker_news_context(
                ticker, fresh_capped, stale_capped, as_of=as_of,
            )

        # ── 5. Emit one state_delta event ────────────────────────────────
        delta: dict[str, object] = {"temp:news_data": news_data}
        for ticker, block in context_blocks.items():
            delta[f"temp:news_context_{ticker}"] = block
        delta["temp:news_context"] = "\n\n".join(
            context_blocks[ticker] for ticker in tickers
        )

        # Surface trace — no-op unless state["temp:_trace"] is set.
        trace_maybe(state, "01_fetch_news", news_data)

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta=delta),
        )
