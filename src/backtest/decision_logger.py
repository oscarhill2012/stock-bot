"""Per-Fill decision snapshot writer.

Registered as a post-execution hook on the live Executor agent.  Lives outside
the backtest-only path so the RAG-seed corpus also accumulates from live paper
trading once the bot is deployed.

Activated by setting ``state['temp:_decision_logger']`` to a ``DecisionLogger``
instance.  When the key is absent the hook is a no-op — identical posture to
the TraceWriter hook (``state['temp:_trace']``).
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce(value: Any) -> Any:
    """Recursively coerce Pydantic models nested inside dicts/lists.

    Top-level Pydantic instances are dumped via ``.model_dump(mode='json')``.
    Dicts and lists are walked so models nested anywhere in the structure
    are coerced too.  ``datetime`` and ``date`` are normalised to ISO-8601
    strings so producers that emit ``.model_dump()`` (default ``mode='python'``)
    instead of ``.model_dump(mode='json')`` still serialise cleanly — without
    this, raw ``datetime`` leaves (e.g. ``OHLCBar.timestamp``,
    ``NewsArticle.published_at``, ``Filing.filed_at``) would hit
    ``_strict_default`` and tank the snapshot write.  JSON primitives
    (None, bool, int, float, str) pass through unchanged.

    Anything else falls through to ``json.dumps``'s default handler,
    which now (via ``_strict_default``) raises ``TypeError`` rather than
    silently emitting ``repr()``.
    """

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")

    # ``datetime`` is a subclass of ``date``; check it first so the more
    # informative ISO representation (with time component) is preserved
    # rather than truncated to a calendar date.
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if isinstance(value, dict):
        return {k: _coerce(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_coerce(v) for v in value]

    return value


def _strict_default(value: Any) -> Any:
    """``json.dumps`` ``default=`` handler — raise loudly on unsupported types.

    The previous ``default=str`` quietly emitted ``repr(value)`` for any
    type ``json.dumps`` did not recognise — that is exactly how the
    ``Form4Bundle`` regression slipped in (the model instance got
    ``repr``'d into a 2 292-char string).  Forcing a ``TypeError`` here
    means any new un-dumpable field shows up immediately as a failing
    backtest rather than as a silently-corrupted decision row.
    """

    raise TypeError(
        f"decision_logger: refusing to serialise {type(value).__name__} "
        f"— add an explicit ``.model_dump()`` at the producing call site "
        f"or extend _coerce to handle this shape"
    )


def _serialise_snapshot(snapshot: dict) -> str:
    """Public entry point for the strict snapshot serialiser.

    Coerces nested Pydantic models via ``_coerce`` then runs
    ``json.dumps`` with the strict default handler.  Tests can call this
    directly to pin the contract.

    Parameters
    ----------
    snapshot:
        The raw decision snapshot dict, potentially containing Pydantic
        model instances at any nesting depth.

    Returns
    -------
    str
        A JSON string with two-space indentation.  Raises ``TypeError``
        if any value cannot be serialised after coercion.
    """

    coerced = _coerce(snapshot)
    return json.dumps(coerced, indent=2, default=_strict_default)


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
            ``tick_id``, ``temp:ticker_evidence_objects`` (list of per-ticker
            TickerEvidence dumps), ``strategist_decision`` (with ``stances``
            list, ``reasoning``, ``thesis``, ``decision_tag``,
            ``confidence``), ``positions`` (held-position thesis book keyed
            by ticker), ``clamps``.
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
                    _serialise_snapshot(snapshot),
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
        # Pull analyst evidence for this ticker.  The context shim writes a
        # *list* of TickerEvidence dumps under ``temp:ticker_evidence_objects``;
        # find the entry whose ``ticker`` field matches.  Absent / empty list →
        # empty dict so downstream serialisation does not see ``None``.
        ev_objects = state.get("temp:ticker_evidence_objects") or []
        ev_view: dict = next(
            (e for e in ev_objects if isinstance(e, dict) and e.get("ticker") == ticker),
            {},
        )

        # Strategist decision fields.  The strategist emits a *list* of
        # ``TickerStance`` objects under ``stances`` (not a per-ticker dict
        # under ``ticker_stances``); find the stance whose ``ticker`` matches.
        decision      = state.get("strategist_decision") or {}
        stances_list  = decision.get("stances") or []
        stance: dict  = next(
            (s for s in stances_list if isinstance(s, dict) and s.get("ticker") == ticker),
            {},
        )
        # iter-3 rename: ``close_reasons`` → ``sell_reasons``.
        # Read ``sell_reasons`` first; fall back to ``close_reasons`` for
        # legacy session state that was persisted before the rename landed.
        close_reason  = (
            (decision.get("sell_reasons") or decision.get("close_reasons") or {})
            .get(ticker, "")
        )

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
            # A2.6: raw analyst-input dicts now live under ``temp:``-prefixed
            # keys so ADK strips them at the invocation boundary.  The logger
            # reads the same invocation's state, so the prefixed keys are still
            # reachable here (they haven't been stripped yet).
            "analyst_inputs": {
                "technical": state.get("temp:technical_data", {}).get(ticker) if state.get("temp:technical_data") else None,
                "fundamental": state.get("temp:fundamental_data", {}).get(ticker) if state.get("temp:fundamental_data") else None,
                "news": state.get("temp:news_data", {}).get(ticker) if state.get("temp:news_data") else None,
                # smart_money_data values are SmartMoneyRaw model instances
                # (Phase 7.6) — coerce to dict so JSON dump gets structured
                # fields rather than the model repr.
                "smart_money": _coerce(state.get("smart_money_data", {}).get(ticker)) if state.get("smart_money_data") else None,
                "social": state.get("temp:social_data", {}).get(ticker) if state.get("temp:social_data") else None,
            },

            # Per-analyst verdict + rationale emitted into evidence_view.
            # In phase F this is a dict keyed by analyst domain; richer
            # structured typing (TickerEvidence) is an iteration-surface item.
            "analyst_outputs": _coerce(ev_view),

            "strategist_view": {
                # TickerEvidence aggregate — same as analyst_outputs in v1;
                # kept as a distinct field so the schema can diverge later.
                "ticker_evidence": _coerce(ev_view),
                # Full PositionThesis dump for the held position at fill time.
                # The strategist's context shim writes the structured book
                # under ``state["positions"]`` (a dict[ticker → thesis_dump]);
                # ``None`` here means the ticker was flat going into the tick.
                "held_view_at_decision": _coerce(
                    (state.get("positions") or {}).get(ticker)
                ),
            },

            # Full strategist accountability payload — used by the future
            # persistent-memory loop to retrieve past decisions by reasoning /
            # decision_tag and compare against realised outcomes.  Keep the
            # tick-level reasoning and thesis as full strings (not
            # truncated excerpts): the RAG corpus needs the real text, and
            # one decision per fill is the right granularity to pay that cost.
            "strategist_decision": {
                "stance":       _coerce(stance),
                "close_reason": close_reason,
                "reasoning":    decision.get("reasoning", ""),
                "thesis":       decision.get("thesis", ""),
                "decision_tag": decision.get("decision_tag", ""),
                "confidence":   decision.get("confidence"),
            },

            "risk_gate": {
                "clamps": [_coerce(c) for c in ticker_clamps],
            },

            # Backfilled by reporting.py after the window completes.
            "forward_returns": None,
        }
