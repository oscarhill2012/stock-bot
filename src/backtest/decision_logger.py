"""Per-Fill decision snapshot writer.

Registered as a post-execution hook on the live Executor agent.  Lives outside
the backtest-only path so the RAG-seed corpus also accumulates from live paper
trading once the bot is deployed.

Activated by setting ``state['_decision_logger']`` to a ``DecisionLogger``
instance.  When the key is absent the hook is a no-op — identical posture to
the TraceWriter hook (``state['_trace']``).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce(value: Any) -> Any:
    """Best-effort JSON-friendly coercion for Pydantic-or-dict mixed payloads.

    Pydantic v2 models expose ``model_dump``; plain dicts and primitives are
    returned as-is.
    """
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _slug(as_of: Any) -> str:
    """Return a filename-safe ISO timestamp slug.

    Replaces colons and plus-signs that are unsafe on Windows (and in some
    shells) with dash-equivalents.  Example::

        "2023-03-13T09:30:00-04:00"  →  "2023-03-13T09-30-00-04-00"
    """
    return (
        str(as_of)
        .replace(":", "-")
        .replace("+", "p")
        .replace(" ", "T")
    )


# ---------------------------------------------------------------------------
# DecisionLogger
# ---------------------------------------------------------------------------

class DecisionLogger:
    """Writes one self-contained JSON snapshot per executed (non-rejected) Fill.

    Each snapshot file is named::

        <as_of-slug>__<TICKER>__<side>.json

    and captures the full decision context at the moment of execution —
    analyst inputs/outputs, strategist stance, risk-gate clamps, fill details —
    with ``forward_returns`` left as ``null`` until ``reporting.py`` backfills
    it at end-of-window.
    """

    def __init__(self, output_dir: Path, window_key: str) -> None:
        """Initialise the writer.

        Parameters
        ----------
        output_dir:
            Directory to write snapshot files into.  Created if absent.
        window_key:
            Era slug recorded in the per-decision ``tick.window_key`` field
            (e.g. ``"svb-stress-2023-03"``).
        """
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._window_key = window_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_executions(self, state: dict) -> None:
        """Walk ``state['executions']`` and write one file per filled order.

        Orders with ``status != "filled"`` (e.g. rejected) are silently
        skipped.  Any write error is caught and logged so a logging failure
        can never abort the tick.

        Parameters
        ----------
        state:
            The session state dict produced by the executor after the tick.
            Expected keys: ``executions``, ``as_of``, ``tick_phase``,
            ``tick_id``, ``evidence_view``, ``strategist_decision``, ``clamps``.
        """
        executions = state.get("executions", [])
        as_of = state.get("as_of")
        phase = state.get("tick_phase", "")

        for ex in executions:
            if ex.get("status") != "filled":
                continue

            order = ex["order"]

            # Normalise the order dict if it is a Pydantic model.
            if hasattr(order, "model_dump"):
                order = order.model_dump()

            ticker = order["ticker"]
            side = order["action"].lower()

            snapshot = self._build_snapshot(
                state, ex, order,
                ticker=ticker,
                side=side,
                as_of=as_of,
                phase=phase,
            )

            slug = _slug(as_of)
            outpath = self._dir / f"{slug}__{ticker}__{side}.json"

            try:
                outpath.write_text(
                    json.dumps(snapshot, indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception:
                # Never let a logger failure propagate up and abort the tick.
                _log.exception("failed to write decision snapshot %s", outpath)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_snapshot(
        self,
        state: dict,
        ex: dict,
        order: dict,
        *,
        ticker: str,
        side: str,
        as_of: Any,
        phase: str,
    ) -> dict:
        """Assemble one self-contained decision JSON object.

        Parameters
        ----------
        state:
            Full session state.
        ex:
            The raw execution record (one element of ``state["executions"]``).
        order:
            The order sub-dict (already normalised to plain dict).
        ticker:
            Ticker symbol for this fill.
        side:
            ``"buy"`` or ``"sell"`` (lower-cased action).
        as_of:
            Tick timestamp (may be a string or datetime).
        phase:
            Tick phase label (``"open"`` / ``"close"``).

        Returns
        -------
        dict
            The JSON-serialisable decision snapshot.
        """
        # Pull analyst evidence for this ticker (may be absent — logs null).
        ev_view = state.get("evidence_view", {}).get(ticker, {})

        # Strategist decision fields.
        decision = state.get("strategist_decision") or {}
        stance = (decision.get("ticker_stances") or {}).get(ticker, {})
        close_reason = (decision.get("close_reasons") or {}).get(ticker, "")

        # Risk-gate clamps that concern this ticker specifically.
        all_clamps = state.get("clamps", []) or []
        ticker_clamps = [
            c for c in all_clamps
            if (c.get("ticker") if isinstance(c, dict) else getattr(c, "ticker", None)) == ticker
        ]

        return {
            "decision_id": f"{_slug(as_of)}__{ticker}__{side}",

            "tick": {
                "as_of": str(as_of),
                "phase": phase,
                "window_key": self._window_key,
                "tick_id": state.get("tick_id"),
            },

            "ticker": ticker,
            "side": side,

            "execution": {
                "order_qty": order.get("quantity"),
                "fill_price": ex.get("actual_price"),
                "fill_qty": ex.get("actual_quantity"),
                "status": ex.get("status"),
                "broker_order_id": ex.get("broker_order_id"),
                "slippage_bps": ex.get("slippage_bps"),
            },

            # Raw per-domain data fed to analysts — pulled from session state.
            # Fields are null when the analyst domain did not run (e.g. social
            # is disabled in backtest mode).
            "analyst_inputs": {
                "technical": state.get("technical_data", {}).get(ticker) if state.get("technical_data") else None,
                "fundamental": state.get("fundamental_data", {}).get(ticker) if state.get("fundamental_data") else None,
                "news": state.get("news_data", {}).get(ticker) if state.get("news_data") else None,
                "smart_money": state.get("smart_money_data", {}).get(ticker) if state.get("smart_money_data") else None,
                "social": state.get("social_data", {}).get(ticker) if state.get("social_data") else None,
            },

            # Per-analyst verdict + rationale emitted into evidence_view.
            # In phase F this is a dict keyed by analyst domain; richer
            # structured typing (TickerEvidence) is an iteration-surface item.
            "analyst_outputs": _coerce(ev_view),

            "strategist_view": {
                # TickerEvidence aggregate — same as analyst_outputs in v1;
                # kept as a distinct field so the schema can diverge later.
                "ticker_evidence": _coerce(ev_view),
                # Previously-held stance (from prior tick) — null if first tick
                # or if the state key is absent.
                "held_view_at_decision": _coerce(
                    state.get("held_view", {}).get(ticker)
                    if state.get("held_view") else None
                ),
            },

            "strategist_decision": {
                "stance": _coerce(stance),
                "close_reason": close_reason,
                # Short excerpt from the LLM reasoning string, if available.
                "reasoning_excerpt": decision.get("reasoning_excerpt", ""),
            },

            "risk_gate": {
                "clamps": [_coerce(c) for c in ticker_clamps],
            },

            # Backfilled by reporting.py after the window completes.
            "forward_returns": None,
        }
