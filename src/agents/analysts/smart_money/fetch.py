"""Smart-money fetch callback — external-observer flows only.

Phase 5 re-categorisation: smart_money is scoped to signals from external
sophisticated observers (congressional trades, notable 13F holders).  Insider
trades (Form 4) belong to the Fundamental analyst, which has the prose-reading
mandate (MD&A, risk factors, Form 4 footnotes) that justifies an LLM.

The callback writes ``state["smart_money_data"]`` as:

.. code-block:: python

    {
        "politicians": {ticker: [filing_dict, ...]},
        "notable_holders": {ticker: [holder_dict, ...]},
    }

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

    smart_money_data: dict = {
        "politicians": {},
        "notable_holders": {},
    }

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
                ticker, lookback_days=holder_lookback_days, as_of=as_of
            )
        except Exception as exc:
            logger.warning("notable_holders fetch failed for %s: %s", ticker, exc)
            holders = []

        smart_money_data["politicians"][ticker] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in politicians
        ]
        smart_money_data["notable_holders"][ticker] = [
            h.model_dump() if hasattr(h, "model_dump") else h for h in holders
        ]

    state["smart_money_data"] = smart_money_data

    # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
    _trace_maybe(state, "01_fetch_smart_money", smart_money_data)

    # Return None unconditionally so ADK does NOT set end_invocation=True.
    # Per-ticker no-data handling is delegated to _run_async_impl via the
    # ``is_no_data=1.0`` feature flag in extract_smart_money_features.
    return None
