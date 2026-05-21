"""FundamentalFetchAgent — BaseAgent that fetches fundamental data for every watchlist ticker.

Phase 9 introduced the per-ticker fan-out design: the batched
``FundamentalAnalyst`` LlmAgent (one prompt for all tickers) was replaced with
a fan-out of per-ticker LlmAgents.  Each branch needs its own context key —
``temp:fundamental_context_<TICKER>`` — populated with only that ticker's data
so ADK's ``inject_session_state`` fills ``{fundamental_context}`` with
single-ticker text.

The legacy ``fundamental_fetch_callback`` (a ``before_agent_callback`` on the
batched ``FundamentalAnalyst`` LlmAgent) was retired when this agent landed
— see ``agents.analysts.fundamental.fetch`` for the retained formatting helpers.

This agent runs ONCE per tick, fetching ratios, filings, and insider trades
for every watchlist ticker, then writing one context key per ticker.  The
design mirrors ``agents.analysts.news.fetch_agent.NewsFetchAgent`` exactly.

Yielded state_delta keys (one event):
  - ``temp:fundamental_data``
        Machine-readable triad dict keyed by ticker::

            {
                "<TICKER>": {
                    "ratios":  dict | None,
                    "filings": [dict, ...],
                    "insider": Form4Bundle,
                }
            }

  - ``temp:fundamental_context_<TICKER>``
        Formatted text block for one ticker; consumed by that ticker's
        ``FundamentalAnalyst_<TICKER>`` LlmAgent via the
        ``{fundamental_context}`` placeholder.

  - ``temp:fundamental_context``
        Aggregate joined block (all tickers); retained for trace/debug
        surfaces per Phase 9 spec §1.  Per-ticker LlmAgents do NOT read
        this key — they read their own ``temp:fundamental_context_<TICKER>``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

# Reuse the per-ticker formatter and provider-interface adapter from the
# shared helpers module.  Keeping the formatter in one place ensures the
# context text is consistent across all call sites.
from agents.analysts.fundamental.fetch import _build_ticker_fundamental_context
from data import get_company_filings, get_company_ratios, get_insider_trades
from data.config import get_config
from data.models import Form4Bundle
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe

_LOGGER = logging.getLogger(__name__)


class FundamentalFetchAgent(BaseAgent):
    """Fetch ratios, filings, and insider trades for every watchlist ticker.

    Reads ``state["tickers"]`` and ``state["as_of"]``; writes
    ``temp:fundamental_data`` and one ``temp:fundamental_context_<TICKER>``
    per ticker via a single ``state_delta`` event.

    Provider failures are tolerated per-ticker — a broken ratios or filings
    call for one ticker falls back to ``None`` / ``[]`` without aborting the
    fetch for other tickers.
    """

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Fetch fundamental data for all watchlist tickers; yield one state_delta event.

        Iterates over ``state["tickers"]``, calling three providers per ticker
        (company_ratios, company_filings, insider_trades).  Each provider call
        is wrapped in its own try/except so a partial failure degrades gracefully
        rather than aborting the entire tick.

        Args:
            ctx: ADK invocation context carrying the session state.

        Yields:
            One ``Event`` whose ``actions.state_delta`` carries:
              - ``temp:fundamental_data`` — machine-readable per-ticker triad
              - ``temp:fundamental_context_<TICKER>`` — formatted single-ticker block
              - ``temp:fundamental_context`` — aggregate multi-ticker block for traces
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []

        # Resolve the historical clock (backtest replay) or wall-clock (live run).
        as_of: datetime = resolve_as_of(
            state.get("as_of"), allow_wallclock=True, site="fundamental/fetch_agent",
        )

        # Read fetch parameters from config once per invocation so the agent
        # stays in lock-step with the backtest cache-fill (which reads the same
        # config keys).  Never hardcode these values inline.
        defaults = get_config().defaults
        insider_lookback_days: int   = defaults.insider_lookback_days
        filings_per_form: int        = defaults.filings_per_form
        include_filing_excerpts: bool = defaults.include_filing_excerpts

        fundamental_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        for ticker in tickers:

            # --- Company ratios ---
            # Only the scalar ratios come through (no OHLCV history); the
            # Technical analyst is the sole consumer of bars (Phase 5 split).
            try:
                ratios_obj = await get_company_ratios(ticker, as_of=as_of)
                ratios_payload = (
                    ratios_obj.model_dump()
                    if hasattr(ratios_obj, "model_dump")
                    else ratios_obj
                )
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                _LOGGER.warning("company_ratios fetch failed for %s: %s", ticker, exc)
                ratios_payload = None

            # --- SEC filings ---
            try:
                filings = await get_company_filings(
                    ticker,
                    as_of=as_of,
                    limit=filings_per_form,
                    include_excerpts=include_filing_excerpts,
                )
                # Serialise to plain dicts so downstream consumers (extractor,
                # cache callbacks) always see a consistent shape.
                filings_payload = [
                    f.model_dump() if hasattr(f, "model_dump") else f
                    for f in filings
                ]
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                _LOGGER.warning("filings fetch failed for %s: %s", ticker, exc)
                filings_payload = []

            # --- Insider trades (Form 4) ---
            try:
                insider_bundle = await get_insider_trades(
                    ticker, lookback_days=insider_lookback_days, as_of=as_of,
                )
                # Guard against unexpected return types from provider variants.
                if not isinstance(insider_bundle, Form4Bundle):
                    _LOGGER.warning(
                        "insider_trades for %s returned %s, expected Form4Bundle"
                        " — using empty bundle",
                        ticker,
                        type(insider_bundle).__name__,
                    )
                    insider_bundle = Form4Bundle(trades=[], derivatives=[])
            except Exception as exc:  # noqa: BLE001 — degrade gracefully
                _LOGGER.warning("insider_trades fetch failed for %s: %s", ticker, exc)
                insider_bundle = Form4Bundle(trades=[], derivatives=[])

            fundamental_data[ticker] = {
                "ratios":  ratios_payload,
                "filings": filings_payload,
                "insider": insider_bundle,
            }

            # Build the formatted LLM-readable block for this ticker.
            # ``_build_ticker_fundamental_context`` adapts the dict interface
            # to the underlying ``_build_ticker_context`` positional signature.
            per_ticker_blocks[ticker] = _build_ticker_fundamental_context(
                ticker, fundamental_data[ticker],
            )

        # Build the state_delta payload.  All keys are ``temp:``-prefixed so
        # ADK strips them at the invocation boundary (Rule 2).
        delta: dict[str, object] = {"temp:fundamental_data": fundamental_data}

        # Write one per-ticker key — each ticker's LlmAgent reads ONLY its own.
        for ticker, block in per_ticker_blocks.items():
            delta[f"temp:fundamental_context_{ticker}"] = block

        # Retain the aggregate ``temp:fundamental_context`` key — the multi-
        # ticker joined block — for trace/debug surfaces (Phase 9 spec §1).
        # Per-ticker LlmAgents read their own ``temp:fundamental_context_<TICKER>``;
        # this aggregate key is only for human-readable traces.
        delta["temp:fundamental_context"] = "\n\n".join(
            f"=== {t} ===\n{per_ticker_blocks[t]}" for t in tickers
        )

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        _trace_maybe(state, "01_fetch_fundamental", fundamental_data)

        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta=delta),
        )
