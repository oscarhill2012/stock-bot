"""Per-Fill decision snapshot writer.

Registered as a post-execution hook on the live pipeline.  Lives outside the
backtest-only path so the RAG-seed corpus also accumulates from live paper
trading once the bot is deployed.  Activated by setting
``state['_decision_logger']`` to a ``DecisionLogger`` instance; absent that
key the hook is a no-op (same posture as the trace writer).

Output files are written to ``<output_dir>/<as_of>__<TICKER>__<side>.json``,
one per executed (non-rejected) Fill.  ``forward_returns`` is always ``null``
at write time; ``reporting.py`` back-fills it at end-of-window.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _coerce(value: Any) -> Any:
    """Best-effort JSON-friendly coercion for Pydantic-or-dict mixed payloads.

    Pydantic v2 models are serialised via ``model_dump()``.  Plain dicts and
    other primitive types are returned as-is.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _slug(as_of: Any) -> str:
    """Return a filename-safe ISO timestamp slug from an ``as_of`` value.

    Replaces colons and plus signs so the string is safe on all filesystems.
    Examples:
        ``"2023-03-13T09:30:00-04:00"``  →  ``"2023-03-13T09-30-00-04-00"``
    """
    return (
        str(as_of)
        .replace(":", "-")
        .replace("+", "p")
        .replace(" ", "T")
    )


class DecisionLogger:
    """Writes one JSON snapshot per executed (non-rejected) order.

    Instantiated once per backtest run (and, eventually, per live-paper run).
    The Executor calls ``on_executions(state)`` after each tick; all context
    needed for the snapshot is read directly from session state.

    Parameters
    ----------
    output_dir:
        Directory to write ``<as_of>__<TICKER>__<side>.json`` files into.
        Created on construction if absent.
    window_key:
        Era slug recorded in the per-decision ``tick.window_key`` field.
        Pass an empty string or ``"live"`` for non-backtest contexts.
    """

    def __init__(self, output_dir: Path, window_key: str) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._window_key = window_key

    # ------------------------------------------------------------------
    # Public hook — called by ExecutorAgent after each tick
    # ------------------------------------------------------------------

    def on_executions(self, state: dict) -> None:
        """Walk ``state['executions']`` and write one file per filled order.

        Only ``status == "filled"`` executions produce a snapshot; rejected
        and any other non-filled statuses are silently skipped.

        Parameters
        ----------
        state:
            A copy of the ADK session state after the Executor has run.
        """
        executions = state.get("executions", [])
        as_of = state.get("as_of")
        phase = state.get("tick_phase", "")

        for ex in executions:
            if ex.get("status") != "filled":
                continue

            order = ex["order"]
            ticker = order["ticker"]
            side = order["action"].lower()

            snapshot = self._build_snapshot(
                state, ex,
                ticker=ticker,
                side=side,
                as_of=as_of,
                phase=phase,
            )

            slug = _slug(as_of)
            outpath = self._dir / f"{slug}__{ticker}__{side}.json"

            try:
                outpath.write_text(json.dumps(snapshot, indent=2, default=str))
            except Exception:
                # A logger failure must never abort the tick or the run.
                log.exception("Failed to write decision snapshot %s", outpath)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        state: dict,
        ex: dict,
        *,
        ticker: str,
        side: str,
        as_of: Any,
        phase: str,
    ) -> dict:
        """Assemble one self-contained decision JSON object.

        Reads all relevant context from ``state`` and the single execution
        record ``ex``, returning a dict that matches the schema documented in
        the backtest-harness design spec (section "Decision logger").

        Parameters
        ----------
        state:
            Full session state snapshot.
        ex:
            One entry from ``state["executions"]`` whose status is "filled".
        ticker:
            The ticker symbol for this execution.
        side:
            Lowercase action string (``"buy"`` or ``"sell"``).
        as_of:
            The tick timestamp (ISO string or datetime).
        phase:
            ``"open"`` or ``"close"`` tick phase.

        Returns
        -------
        dict
            JSON-serialisable decision snapshot.
        """
        # Per-ticker analyst evidence view (raw or Pydantic — coerce either way).
        ev_view = state.get("evidence_view", {}).get(ticker, {})

        # Strategist decision block — tolerates missing / None.
        decision = state.get("strategist_decision") or {}
        stance = (decision.get("ticker_stances") or {}).get(ticker, {})
        close_reason = (decision.get("close_reasons") or {}).get(ticker, "")

        # Risk-gate clamps scoped to this ticker only.
        all_clamps = state.get("clamps", []) or []
        ticker_clamps = [c for c in all_clamps if _clamp_ticker(c) == ticker]

        return {
            "decision_id": f"{_slug(as_of)}__{ticker}__{side}",

            # Tick context — backfilled with window_key for corpus organisation.
            "tick": {
                "as_of":      str(as_of),
                "phase":      phase,
                "window_key": self._window_key,
                "tick_id":    state.get("tick_id"),
            },

            "ticker": ticker,
            "side":   side,

            # Execution details — what the broker actually filled.
            "execution": {
                "order_qty":      ex["order"]["quantity"],
                "fill_price":     ex.get("actual_price"),
                "fill_qty":       ex.get("actual_quantity"),
                "status":         ex.get("status"),
                "broker_order_id": ex.get("broker_order_id"),
                "slippage_bps":   ex.get("slippage_bps"),
            },

            # Raw data that was fed to each analyst for this ticker.
            "analyst_inputs": {
                "technical":   state.get("technical_data",   {}).get(ticker),
                "fundamental": state.get("fundamental_data", {}).get(ticker),
                "news":        state.get("news_data",        {}).get(ticker),
                "smart_money": state.get("smart_money_data", {}).get(ticker),
                "social":      state.get("social_data",      {}).get(ticker),
            },

            # Analyst verdicts + rationale.
            "analyst_outputs": _coerce(ev_view),

            # Strategist's aggregated view at the moment of decision.
            "strategist_view": {
                "ticker_evidence":       _coerce(ev_view),
                "held_view_at_decision": _coerce(
                    state.get("held_view", {}).get(ticker)
                ),
            },

            # What the strategist decided and why.
            "strategist_decision": {
                "stance":            _coerce(stance),
                "close_reason":      close_reason,
                "reasoning_excerpt": decision.get("reasoning_excerpt", ""),
            },

            # Risk-gate clamp log for this ticker.
            "risk_gate": {
                "clamps": _coerce(ticker_clamps),
            },

            # Populated by reporting.py at end-of-window; null at write time.
            "forward_returns": None,
        }


def _clamp_ticker(clamp: Any) -> str | None:
    """Extract the ticker from a clamp record (dict or Pydantic model).

    Returns ``None`` if the ticker cannot be determined — the caller will
    then exclude the clamp from the per-ticker list.
    """
    if isinstance(clamp, dict):
        return clamp.get("ticker")
    return getattr(clamp, "ticker", None)
