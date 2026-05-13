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

If neither source yields any activity across all tickers, a skip-Content is
returned to short-circuit the LLM call and pre-seed an empty
``smart_money_verdicts`` list so the downstream evidence-writer sees a clean
no-data state rather than a missing key.
"""
from __future__ import annotations

import logging

from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from data import (
    get_notable_holders,
    get_public_figure_trades,
)

POLITICIAN_LOOKBACK_DAYS = 30
HOLDER_LOOKBACK_DAYS = 90

logger = logging.getLogger(__name__)


async def smart_money_fetch_callback(
    callback_context: CallbackContext,
) -> genai_types.Content | None:
    """Fetch smart-money data; return Content to skip LLM if no signal detected.

    Pulls congressional / public-figure trades and notable 13F holders for
    every ticker in ``state["tickers"]``.  Insider trades are deliberately
    excluded — they are now fetched by the Fundamental analyst's callback.

    Parameters
    ----------
    callback_context:
        ADK callback context.  ``callback_context.state["tickers"]`` must be a
        list of ticker strings.

    Returns
    -------
    google.genai.types.Content | None
        A skip-Content if no politician or holder activity was found (causing
        ADK to bypass the LLM call for this analyst), or ``None`` to let the
        LLM proceed normally.
    """
    state = callback_context.state
    tickers: list[str] = state.get("tickers", [])

    smart_money_data: dict = {
        "politicians": {},
        "notable_holders": {},
    }
    has_signal = False

    for ticker in tickers:
        try:
            politicians = await get_public_figure_trades(
                ticker, lookback_days=POLITICIAN_LOOKBACK_DAYS
            )
        except Exception as exc:
            logger.warning("politician_trades fetch failed for %s: %s", ticker, exc)
            politicians = []

        try:
            holders = await get_notable_holders(ticker, lookback_days=HOLDER_LOOKBACK_DAYS)
        except Exception as exc:
            logger.warning("notable_holders fetch failed for %s: %s", ticker, exc)
            holders = []

        smart_money_data["politicians"][ticker] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in politicians
        ]
        smart_money_data["notable_holders"][ticker] = [
            h.model_dump() if hasattr(h, "model_dump") else h for h in holders
        ]

        if politicians or holders:
            has_signal = True

    state["smart_money_data"] = smart_money_data

    if not has_signal:
        # Pre-seed an empty verdicts list so the after-callback
        # (make_evidence_callback) short-circuits cleanly and synthesises
        # no-data evidence for every ticker rather than raising KeyError on
        # an absent key.
        state["smart_money_verdicts"] = []
        return genai_types.Content(
            parts=[genai_types.Part(text="no smart money signal — skipping")],
            role="model",
        )

    return None
