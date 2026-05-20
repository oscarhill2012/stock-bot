"""StrategistContextShim — ADK BaseAgent that hydrates strategist context keys.

Replaces the two ``before_agent_callback`` direct-mutation sites on the
Strategist ``LlmAgent`` (``_held_view_before_callback`` and
``_evidence_view_before_callback`` in ``agents/strategist/agent.py``).

ADK callbacks cannot yield ``Event``s (contract Rule 3) but the contract
requires every state write to ride on a yielded
``Event(actions=EventActions(state_delta=...))`` (Rule 1).  The shim
resolves the conflict: the same view-rendering work runs inside a
``BaseAgent._run_async_impl``, which can yield.  The shim slots in front
of the Strategist LlmAgent inside a SequentialAgent so the LlmAgent's
``inject_session_state`` resolves ``{temp:held_positions_view}`` and
``{temp:ticker_evidence}`` against the freshly-written state.

The three keys carry the ``temp:`` prefix mandated by §C-Rule 2 — they are
invocation-scoped working state, never read across ticks.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.strategist.held_view import render_held_positions_view
from broker.portfolio import Portfolio
from contract.digest import build_ticker_evidence
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS
from contract.evidence import AnalystEvidence
from contract.strategist_prompt import render_all_ticker_blocks
from contract.ticker_evidence import TickerEvidence
from data.timeguard import resolve_as_of
from observability.trace import _trace_maybe


def _coerce_portfolio(value) -> Portfolio:
    """Return a Portfolio whether ``value`` is an instance, dict, or None.

    Mirrors the helper in ``agents.strategist.agent`` so the shim is
    self-contained and does not pull in callback-flavoured code.

    Args:
        value: A ``Portfolio``, a ``Portfolio.model_dump(mode="json")``
            dict, or ``None``.

    Returns:
        A ``Portfolio`` instance.  ``None`` produces an empty portfolio.
    """
    if isinstance(value, Portfolio):
        return value
    if value is None:
        return Portfolio(cash=0.0)
    return Portfolio.model_validate(value)


def _index_evidence(state, key: str) -> dict[str, AnalystEvidence]:
    """Index a per-analyst evidence list by ticker.

    Items may be raw dicts (post-JSON-serialisation) or validated
    ``AnalystEvidence`` instances — both are tolerated.

    Args:
        state: ADK session-state proxy / dict.
        key: The state key, e.g. ``"technical_evidence"``.

    Returns:
        Mapping ticker -> ``AnalystEvidence``.
    """
    items = state.get(key, []) or []
    out: dict[str, AnalystEvidence] = {}
    for item in items:
        ev = AnalystEvidence.model_validate(item) if isinstance(item, dict) else item
        out[ev.ticker] = ev
    return out


class StrategistContextShim(BaseAgent):
    """Hydrate ``temp:held_positions_view`` + ``temp:ticker_evidence*`` on state.

    Yields a single ``Event(state_delta=…)`` carrying the three keys the
    Strategist's instruction template will resolve.  Slots immediately
    before the Strategist ``LlmAgent`` inside its enclosing
    ``SequentialAgent``.

    Why this is a ``BaseAgent`` not a callback: ADK callbacks cannot
    yield ``Event``s (Rule 3); state writes must ride on
    ``state_delta`` (Rule 1).  A ``BaseAgent`` is the smallest legal
    construct that satisfies both rules.
    """

    name: str = "StrategistContextShim"

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Build held-view + ticker-evidence and emit them on a single Event.

        Reads ``positions``, ``portfolio``, ``tickers``, ``tick_id``,
        ``as_of`` / ``recorded_at``, and the four per-analyst
        ``*_evidence`` lists.  Writes ``temp:held_positions_view``,
        ``temp:ticker_evidence``, and ``temp:ticker_evidence_objects``.

        Args:
            ctx: ADK invocation context; ``ctx.session.state`` is the
                pipeline session-state dict / proxy.

        Yields:
            Exactly one ``Event`` whose ``actions.state_delta`` carries
            the three context keys.
        """
        state = ctx.session.state

        # ── Held-positions view ───────────────────────────────────────────
        positions = state.get("positions", {}) or {}
        portfolio = _coerce_portfolio(state.get("portfolio"))
        held_view = render_held_positions_view(positions, portfolio)

        # ── Ticker-evidence view ──────────────────────────────────────────
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str = state.get("tick_id", "unknown")

        # Resolve the ``recorded_at`` timestamp for evidence aggregation.
        # Priority: state["as_of"] (backtest replay clock) > state["recorded_at"]
        # > wall-clock fallback (live, when STOCKBOT_STRICT_AS_OF=0).
        as_of_raw = state.get("as_of")
        if isinstance(as_of_raw, datetime):
            recorded_at = as_of_raw
        else:
            recorded_at_raw = state.get("recorded_at")
            if isinstance(recorded_at_raw, str):
                recorded_at = datetime.fromisoformat(recorded_at_raw)
            elif isinstance(recorded_at_raw, datetime):
                recorded_at = recorded_at_raw
            else:
                recorded_at = resolve_as_of(
                    None, allow_wallclock=True, site="strategist/context_shim",
                )

        # Index every analyst's evidence list by ticker.
        tech = _index_evidence(state, "technical_evidence")
        fund = _index_evidence(state, "fundamental_evidence")
        news = _index_evidence(state, "news_evidence")
        sm   = _index_evidence(state, "smart_money_evidence")

        # Build one TickerEvidence per watchlist ticker.
        ticker_evidence: list[TickerEvidence] = []
        for t in tickers:
            per_analyst: dict[str, AnalystEvidence] = {}
            if t in tech: per_analyst["technical"]   = tech[t]
            if t in fund: per_analyst["fundamental"] = fund[t]
            if t in news: per_analyst["news"]        = news[t]
            if t in sm:   per_analyst["smart_money"] = sm[t]

            te = build_ticker_evidence(
                per_analyst = per_analyst,
                ticker      = t,
                tick_id     = tick_id,
                recorded_at = recorded_at,
                weights     = DEFAULT_ANALYST_WEIGHTS,
            )
            ticker_evidence.append(te)

        ticker_evidence_objects = [te.model_dump(mode="json") for te in ticker_evidence]
        ticker_evidence_rendered = render_all_ticker_blocks(ticker_evidence)

        # Surface trace — no-op unless state["_trace"] is set.
        _trace_maybe(state, "04_digest", ticker_evidence_objects)

        # ── Yield exactly one Event carrying all three keys ───────────────
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "temp:held_positions_view":     held_view,
                "temp:ticker_evidence":         ticker_evidence_rendered,
                "temp:ticker_evidence_objects": ticker_evidence_objects,
            }),
        )
