"""Smart-money fetch callback — external-observer flows only.

Phase 5 re-categorisation: smart_money is scoped to signals from external
sophisticated observers (congressional trades, notable 13F holders).  Insider
trades (Form 4) belong to the Fundamental analyst, which has the prose-reading
mandate (MD&A, risk factors, Form 4 footnotes) that justifies an LLM.

Phase 7.6 Task 17 reshapes the state key.  The callback writes
``state["smart_money_data"]`` as a ticker-first dict:

.. code-block:: python

    {
        "AAPL": SmartMoneyRaw(politicians=[...], notable_holders=[...]),
        "MSFT": SmartMoneyRaw(politicians=[...], notable_holders=[...]),
    }

Values are ``SmartMoneyRaw`` model instances, not plain dicts.  Downstream
consumers access ``raw.politicians`` / ``raw.notable_holders`` as attributes.
``extra="forbid"`` on ``SmartMoneyRaw`` ensures construction-time validation
so typos surface loudly rather than silently producing empty lists.

The callback **always** returns ``None``.  This keeps ADK from setting
``ctx.end_invocation = True`` (which would bypass ``_run_async_impl`` and
prevent the after-agent-callback from firing).  Per-ticker no-data handling
is the responsibility of ``SmartMoneyAnalyst._run_async_impl``, which reads
``smart_money_data``, runs ``extract_smart_money_features``, and emits a
no-data verdict via ``derive_smart_money_verdict`` when ``is_no_data=1.0``.
"""
from __future__ import annotations

import logging
from datetime import datetime

from google.adk.agents.callback_context import CallbackContext

from data import (
    get_notable_holders,
    get_public_figure_trades,
)
from data.models.smart_money import SmartMoneyRaw
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe

logger = logging.getLogger(__name__)


async def smart_money_fetch_callback(
    callback_context: CallbackContext,
) -> None:
    """Fetch smart-money data and write it to state; always returns None.

    Pulls congressional / public-figure trades and notable 13F holders for
    every ticker in ``state["tickers"]``.  Insider trades are deliberately
    excluded — they are now fetched by the Fundamental analyst's callback.

    The function **always** returns ``None`` so ADK does not set
    ``end_invocation = True``.  Returning a ``Content`` object would cause ADK
    to skip ``_run_async_impl`` entirely (see ``BaseAgent.run_async``, line
    476), which would prevent per-ticker no-data verdicts from being emitted
    and block the after-agent-callback from writing evidence.  No-data handling
    is delegated to ``SmartMoneyAnalyst._run_async_impl`` via the
    ``is_no_data=1.0`` feature flag.

    Parameters
    ----------
    callback_context:
        ADK callback context.  ``callback_context.state["tickers"]`` must be a
        list of ticker strings.

    Returns
    -------
    None
        Always — delegates verdict derivation and no-data handling to
        ``_run_async_impl``.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    # Pull the historical clock from session state; default to wall-clock for live.
    as_of: datetime = resolve_as_of(
        state.get("as_of"), allow_wallclock=True, site="smart_money/fetch",
    )

    # Source lookback windows from config — Phase 7.5 makes config/data.json
    # the single source of truth for these values.  Reading inside the
    # callback rather than at module load keeps the import cheap and lets
    # tests monkey-patch the config singleton.
    from data.config import get_config

    defaults = get_config().defaults
    politician_lookback_days = defaults.politician_lookback_days
    holder_lookback_days     = defaults.notable_holder_lookback_days
    # ``notable_holder_limit`` caps the number of 13D/G rows the provider
    # returns per ticker.  Sourced from config so the live tick and backtest
    # cache-fill agree on row counts; without this the dispatcher's
    # hardcoded default (20) silently overrides the configured value.
    holder_limit             = defaults.notable_holder_limit

    # Ticker-first dict: state["smart_money_data"][ticker] → SmartMoneyRaw.
    # SmartMoneyRaw expects list[PoliticianTrade] and list[NotableHolder]; the
    # providers already return typed model instances so we pass them through
    # directly.  Never call .model_dump() here — downstream consumers use
    # attribute access (raw.politicians / raw.notable_holders), not dict keys.
    smart_money_data: dict[str, SmartMoneyRaw] = {}

    for ticker in tickers:
        try:
            politicians = await get_public_figure_trades(
                ticker, lookback_days=politician_lookback_days, as_of=as_of
            )
        except Exception as exc:
            logger.warning("politician_trades fetch failed for %s: %s", ticker, exc)
            politicians = []

        try:
            holders = await get_notable_holders(
                ticker,
                lookback_days=holder_lookback_days,
                limit=holder_limit,
                as_of=as_of,
            )
        except Exception as exc:
            logger.warning("notable_holders fetch failed for %s: %s", ticker, exc)
            holders = []

        # Construct the per-ticker aggregate.  extra="forbid" on SmartMoneyRaw
        # means a ValidationError fires loudly if a field name is wrong, rather
        # than silently dropping data into empty lists.
        smart_money_data[ticker] = SmartMoneyRaw(
            politicians=politicians,
            notable_holders=holders,
        )

    state["smart_money_data"] = smart_money_data

    # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
    _trace_maybe(state, "01_fetch_smart_money", smart_money_data)

    # Return None unconditionally so ADK does NOT set end_invocation=True.
    # Per-ticker no-data handling is delegated to _run_async_impl via the
    # ``is_no_data=1.0`` feature flag in extract_smart_money_features.
    return None
