"""StrategistContextShim — ADK BaseAgent that hydrates strategist context keys.

Replaces the two ``before_agent_callback`` direct-mutation sites on the
Strategist ``LlmAgent`` (``_held_view_before_callback`` and the former
``render_all_ticker_blocks``, now inlined here per A-097.w).

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

Task 9 additions
----------------
- ``temp:first_tick_flag`` — derived from ``user:active_stances_initialised``.
  Renders as the string ``"True"`` when this IS the first tick of a window
  (i.e. ``user:active_stances_initialised`` is absent or ``False``), and
  ``"False"`` on every subsequent tick.  The strategist prompt uses this to
  decide whether to emit one stance per watchlist ticker (first tick) or a
  focused incremental update.  Semantics: "True" = emit a full baseline.

- The held-positions view now shows thesis staleness (ticks since the thesis
  was last updated) and deliberately omits ``horizon``, ``target_price``, and
  ``stop_price`` — those fields were removed in iter-3.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.strategist.prompts import (
    COLD_START_MODE_TEMPLATE,
    FIRST_TICK_PREAMBLE,
    INCREMENTAL_MODE_TEMPLATE,
    INCREMENTAL_PREAMBLE,
)
from broker.portfolio import Portfolio
from contract.digest import DEFAULT_ANALYST_WEIGHTS, build_ticker_evidence
from contract.evidence import AnalystEvidence
from contract.strategist_prompt import render_ticker_block
from contract.ticker_evidence import TickerEvidence
from data.timeguard import resolve_as_of
from observability.trace import trace_maybe


def collapse_repeat_buffer_entries(raw_buffer: list[dict]) -> str:
    """Render the memory buffer for injection into the strategist prompt.

    Collapses consecutive runs of ``is_repeat=True`` / no-execution entries
    into a single summary line so the strategist is not flooded with near-
    identical no-op records.  Every entry that involved an actual execution
    (``executions_count > 0``) or that is NOT marked as a repeat is preserved
    verbatim and in chronological order.

    The collapse is render-time only — the persisted ``state["memory_buffer"]``
    list is never touched, keeping the change fully reversible.

    Strategy
    --------
    Walk the buffer in chronological order.  Maintain a counter for the
    current run of repeat/no-op entries.  When the run ends (either at an
    action entry or at the buffer end), flush the run as a single summary
    line before emitting the action entry.

    Parameters
    ----------
    raw_buffer:
        List of raw buffer-entry dicts as stored in ``state["memory_buffer"]``.
        Each entry must at minimum have ``decision_tag`` (str),
        ``reasoning_summary`` (str), ``is_repeat`` (bool), and
        ``executions_count`` (int).  Missing keys degrade gracefully.

    Returns
    -------
    str
        Human-readable text block ready for splicing into the strategist
        prompt.  Returns a sentinel when the buffer is empty.
    """
    if not raw_buffer:
        return "(no prior ticks this window)"

    lines: list[str] = []

    # Counter tracking the index (1-based, matching the prompt display) of
    # the first entry in the current repeat-run so the summary line can cite
    # "… no action since tick N".
    repeat_run_start_idx: int | None = None
    repeat_run_count: int            = 0

    def _flush_repeat_run(before_idx: int) -> None:
        """Emit a single summary line for a completed repeat run.

        Parameters
        ----------
        before_idx:
            The 1-based display index of the action entry *after* the run
            (or ``len(raw_buffer) + 1`` when flushing at the end of the
            buffer).  Used to phrase the range accurately.
        """
        nonlocal repeat_run_start_idx, repeat_run_count

        if repeat_run_count == 0:
            return

        # Single-entry run: suppress entirely (the next action entry is
        # adjacent, so a summary would be noisier than silence).
        if repeat_run_count == 1 and repeat_run_start_idx is not None:
            idx = repeat_run_start_idx
            entry = raw_buffer[idx - 1]
            tag   = entry.get("decision_tag", "unknown")
            lines.append(f"  [{idx:02d}] {tag}  (no-op / repeat — no change)")
        else:
            # Multi-entry run: collapse to a single line.
            end_idx = before_idx - 1
            lines.append(
                f"  [{repeat_run_start_idx:02d}–{end_idx:02d}] "
                f"no action / repeat across {repeat_run_count} tick(s)"
            )

        repeat_run_start_idx = None
        repeat_run_count     = 0

    for i, entry in enumerate(raw_buffer, start=1):
        is_repeat_entry   = bool(entry.get("is_repeat", False))
        executions_count  = int(entry.get("executions_count", 0))
        is_action         = executions_count > 0

        if is_repeat_entry and not is_action:
            # Start or continue a repeat run.
            if repeat_run_start_idx is None:
                repeat_run_start_idx = i
            repeat_run_count += 1

        else:
            # Flush any preceding repeat run before emitting this action entry.
            _flush_repeat_run(before_idx=i)

            tag     = entry.get("decision_tag", "unknown")
            summary = entry.get("reasoning_summary", "")
            exec_n  = executions_count

            # Action entry: render verbatim so no trade is ever lost from the log.
            lines.append(
                f"  [{i:02d}] {tag}  executions={exec_n}  — {summary}"
            )

    # Flush any trailing repeat run at the end of the buffer.
    _flush_repeat_run(before_idx=len(raw_buffer) + 1)

    total = len(raw_buffer)
    header = f"Memory Buffer (last {total} tick(s)):"
    return header + "\n" + "\n".join(lines)


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

    def render(self, state: dict) -> dict:
        """Compute the synchronous context keys derived from session state.

        This method is the pure computation core — it reads state and returns
        a partial ``state_delta`` dict containing:

        - ``temp:first_tick_flag`` — ``"True"`` when this is the first tick of
          a window (``user:active_stances_initialised`` is absent or ``False``),
          ``"False"`` thereafter.  Still emitted for backward compatibility
          with tests that read the flag directly; the prompt now uses
          ``{temp:first_tick_preamble}`` instead to avoid repeating
          ``first_tick_flag=True/False`` text the model does not need.
        - ``temp:first_tick_preamble`` — the full first-tick guidance block
          (``FIRST_TICK_PREAMBLE``) on the first tick; an empty string on
          every subsequent tick so the placeholder renders to nothing and
          adds zero tokens.
        - ``temp:held_positions_view`` — the lightweight held-positions block
          showing rationale, opened-at, current price/weight/P&L, and thesis
          staleness in ticks.  Intentionally omits ``horizon``,
          ``target_price``, ``stop_price`` (removed in iter-3).

        Separating the pure computation from the ADK plumbing in
        ``_run_async_impl`` lets unit tests call ``render()`` directly without
        constructing a fake ``InvocationContext``.

        Args:
            state: ADK session-state dict / proxy.  Reads the following keys:
                ``user:active_stances_initialised`` (bool, defaults to False),
                ``user:positions`` (dict[ticker, thesis-dict], defaults to {}),
                ``user:current_tick_index`` (int, defaults to 0),
                ``portfolio`` (Portfolio dump, defaults to empty) — sourced so
                the held-view can show live price/weight/P&L per position.

        Returns:
            dict with keys ``temp:first_tick_flag``, ``temp:first_tick_preamble``,
            and ``temp:held_positions_view``.
        """
        # ── Selective-output flag ─────────────────────────────────────────
        # ``user:active_stances_initialised`` is False (or absent) on the
        # first tick of every window, and flipped to True by
        # StrategistEnricher after the first successful LLM call.
        # "True" → this IS the first tick (emit a full baseline).
        # "False" → subsequent tick (incremental update).
        initialised = state.get("user:active_stances_initialised", False)
        first_tick_flag: str = "True" if not initialised else "False"

        # ── Tick-mode preamble — first tick only ──────────────────────────
        # On the first tick of a window the thesis book is empty and the model
        # needs explicit guidance to populate it.  On iterative ticks the
        # ``## Mode`` section and ``## Deployment posture`` already cover the
        # incremental framing, so the preamble collapses to an empty string
        # and adds zero tokens to the prompt.
        first_tick_preamble: str = (
            FIRST_TICK_PREAMBLE if first_tick_flag == "True" else INCREMENTAL_PREAMBLE
        )

        # ── Lightweight held-positions view with staleness ────────────────
        # A-014: read only the canonical user-namespaced key.  The
        # executor's bridge (temp:executor_positions_bridge) is
        # executor-internal and must never leak into the strategist's
        # held-view.
        positions = state.get("user:positions") or {}
        current_tick_index: int = state.get("user:current_tick_index", 0) or 0

        # Portfolio carries live ``last_price`` per held ticker and the cash
        # balance — feed it through so the thesis-book renderer can compute
        # current weight and unrealised P&L without needing extra state keys.
        portfolio = Portfolio.from_state_value(state.get("portfolio"))

        held_view = _render_positions_shim(
            positions,
            current_tick_index = current_tick_index,
            portfolio          = portfolio,
        )

        return {
            "temp:first_tick_flag":     first_tick_flag,
            "temp:first_tick_preamble": first_tick_preamble,
            "temp:held_positions_view": held_view,
        }

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Build held-view + ticker-evidence and emit them on a single Event.

        Reads ``positions``, ``portfolio``, ``tickers``, ``tick_id``,
        ``as_of`` / ``recorded_at``, and the four per-analyst
        ``*_evidence`` lists.  Writes ``temp:first_tick_flag``,
        ``temp:held_positions_view``, ``temp:ticker_evidence``, and
        ``temp:ticker_evidence_objects``.

        The ``temp:held_positions_view`` value is produced by ``render()``
        via the ``_render_positions_shim`` helper below — the lightweight
        thesis-book renderer that shows position state and rationale
        and thesis staleness, and omits horizon/target/stop.

        Args:
            ctx: ADK invocation context; ``ctx.session.state`` is the
                pipeline session-state dict / proxy.

        Yields:
            Exactly one ``Event`` whose ``actions.state_delta`` carries
            the required context keys.
        """
        state = ctx.session.state

        # ── Keys computed by the pure render() helper ─────────────────────
        # Separated so unit tests can call render() directly.
        pure_keys = self.render(state)

        # ── Timestamp resolution for evidence aggregation ─────────────────
        # Priority: state["as_of"] (backtest replay clock) >
        # state["recorded_at"] > wall-clock fallback (live only).
        # NOTE: DatabaseSessionService serialises state via JSON, so ``as_of``
        # may arrive as an ISO-8601 string.  Pass raw to resolve_as_of which
        # handles both datetime and str uniformly.
        as_of_raw = state.get("as_of")
        if as_of_raw is not None:
            recorded_at = resolve_as_of(
                as_of_raw, allow_wallclock=False, site="strategist/context_shim",
            )
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

        # ── Mode header — cold-start vs incremental framing ──────────────
        # Drives the structural diversity of the prompt across ticks.
        # Cold start: portfolio is empty; encourage 1-3 fresh opens.
        # Incremental: emit a stance per held position with a 'what's
        # changed' reason.  See Principle 4 in the spec.
        # A-014: read only the canonical user-namespaced key.  The
        # executor's bridge (temp:executor_positions_bridge) is
        # executor-internal and must never leak into the strategist's
        # held-view.
        positions = state.get("user:positions") or {}
        if not positions:
            mode_text = COLD_START_MODE_TEMPLATE
        else:
            mode_text = INCREMENTAL_MODE_TEMPLATE.format(N=len(positions))

        # ── Ticker-evidence view ──────────────────────────────────────────
        tickers: list[str] = state.get("tickers", []) or []
        tick_id: str = state.get("tick_id", "unknown")

        # Index every analyst's evidence list by ticker.
        tech = _index_evidence(state, "technical_evidence")
        fund = _index_evidence(state, "fundamental_evidence")
        news = _index_evidence(state, "news_evidence")
        sm   = _index_evidence(state, "smart_money_evidence")

        # Coerce the portfolio off state so we can lift live ``last_price`` for
        # held tickers — the most authoritative source since the broker syncs
        # it every tick.  For non-held tickers we fall back to the technical
        # extractor's ``last_close`` feature.
        portfolio = Portfolio.from_state_value(state.get("portfolio"))

        # Build one TickerEvidence per watchlist ticker.
        ticker_evidence: list[TickerEvidence] = []
        for t in tickers:
            per_analyst: dict[str, AnalystEvidence] = {}
            if t in tech:
                per_analyst["technical"]   = tech[t]
            if t in fund:
                per_analyst["fundamental"] = fund[t]
            if t in news:
                per_analyst["news"]        = news[t]
            if t in sm:
                per_analyst["smart_money"] = sm[t]

            # Resolve live price.  Held positions win (broker updates each tick);
            # otherwise read the technical analyst's ``last_close`` feature
            # (the sentinel ``0.0`` indicates the extractor had no bars and
            # we should treat the value as absent).
            # The ``> 0`` guards below are load-bearing: TickerEvidence.last_price
            # is typed ``PositiveFloat | None`` (A-055), so passing 0.0 or a
            # negative would raise a Pydantic ValidationError.  Non-positive values
            # must coerce to ``None`` here, before construction.
            last_price: float | None = None
            held = portfolio.positions.get(t)
            if held is not None and held.last_price > 0:
                last_price = float(held.last_price)
            else:
                tech_ev = per_analyst.get("technical")
                if tech_ev is not None:
                    raw_lc = (tech_ev.features or {}).get("last_close")
                    if raw_lc is not None and float(raw_lc) > 0:
                        last_price = float(raw_lc)

            te = build_ticker_evidence(
                per_analyst = per_analyst,
                ticker      = t,
                tick_id     = tick_id,
                recorded_at = recorded_at,
                weights     = DEFAULT_ANALYST_WEIGHTS,
                last_price  = last_price,
            )
            ticker_evidence.append(te)

        ticker_evidence_objects = [te.model_dump(mode="json") for te in ticker_evidence]
        # Inline of the former ``render_all_ticker_blocks`` (single-caller,
        # inlined per A-097.w).  Concatenates per-ticker prompt blocks
        # separated by a horizontal divider; returns a sentinel when empty.
        if not ticker_evidence:
            ticker_evidence_rendered = "(no evidence this tick)"
        else:
            # Join per-ticker blocks with a horizontal divider for legibility.
            ticker_evidence_rendered = ("\n" + "-" * 60 + "\n").join(
                render_ticker_block(te) for te in ticker_evidence
            )

        # Surface trace — no-op unless state["temp:_trace"] is set.
        trace_maybe(state, "04_digest", ticker_evidence_objects)

        # ── Thesis placeholder ───────────────────────────────────────────
        # The strategist instruction uses the optional ``{user:thesis?}``
        # placeholder.  ADK's ``inject_session_state`` resolves it directly
        # from ``state["user:thesis"]``; when the key is absent (first tick /
        # cold start) the ``?`` suffix causes it to resolve to an empty string
        # rather than raising ``KeyError``.  No bridge from this shim into a
        # bare key is needed.

        # ── Recent round-trips view ──────────────────────────────────────
        # Render the rolling log written by the Executor on every close.
        # One line per closed trade, capped at the most recent 8 so the
        # prompt block stays bounded.  Empty-state copy is explicit so the
        # LLM can distinguish "no trades closed yet" from a missing key.
        recent_trades_view = _render_recent_trades(
            state.get("user:closed_trades_log") or [],
        )

        # ── Memory buffer — collapse repeat/no-op runs at render time ─────
        # The persisted ``state["memory_buffer"]`` list may have up to 24
        # entries, of which many are ``is_repeat=True`` no-ops that carry
        # virtually identical reasoning summaries.  Forwarding all 24 raw
        # entries wastes tokens and buries action entries in noise.
        #
        # Render-time collapse: consecutive repeat/no-op entries are
        # summarised as a single "… no action across N tick(s)" line, while
        # every entry with actual executions (``executions_count > 0``) is
        # preserved verbatim.  The persisted list is NOT modified — this is
        # presentation-only and fully reversible.
        #
        # We inject the result as ``temp:memory_buffer`` (a ``temp:``-prefixed
        # key rendered by the shim) rather than the raw ``memory_buffer`` list
        # (resolved by ADK's inject_session_state as a Python repr string).
        raw_memory_buffer: list[dict] = state.get("memory_buffer") or []
        memory_buffer_rendered = collapse_repeat_buffer_entries(raw_memory_buffer)

        # ── Yield exactly one Event carrying all required keys ────────────
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "temp:strategist_mode":         mode_text,
                # Held-positions view, first-tick flag, and first-tick preamble
                # from the pure render() helper — separated so unit tests can
                # call render() directly without a fake InvocationContext.
                "temp:held_positions_view":     pure_keys["temp:held_positions_view"],
                "temp:first_tick_flag":         pure_keys["temp:first_tick_flag"],
                # First-tick-only guidance block — full text on tick 0 (empty
                # thesis book; model must populate), empty string on all
                # subsequent ticks (Deployment posture + Mode already cover it).
                "temp:first_tick_preamble":     pure_keys["temp:first_tick_preamble"],
                "temp:ticker_evidence":         ticker_evidence_rendered,
                "temp:ticker_evidence_objects": ticker_evidence_objects,
                "temp:recent_trades_view":      recent_trades_view,
                # Rendered (collapsed) memory buffer — injected as a temp
                # key so render-time collapse happens before prompt assembly,
                # not via ADK's raw-list stringify of state["memory_buffer"].
                "temp:memory_buffer":           memory_buffer_rendered,
                # Schema-error feedback slot — empty on the first attempt;
                # the RetryingAgentWrapper overwrites it with the formatted
                # Pydantic validation error before each schema retry so the
                # LLM sees what it got wrong on the previous turn.  The
                # prompt template renders the placeholder verbatim; an empty
                # string yields a blank line that LLMs ignore.
                "temp:_last_schema_error":      "",
            }),
        )


def _render_positions_shim(
    positions: dict,
    *,
    current_tick_index: int,
    portfolio: Portfolio | None = None,
) -> str:
    """Render the thesis book — one row per ticker the agent has a view on.

    The book holds a single row per ticker.  Whether the agent currently
    owns the underlying is metadata on the row, not a different kind of
    row.  The renderer reflects this: one labelled section, with each
    row tagged as ``[POSITION]`` or ``[NO POSITION]`` so the strategist
    sees its exposure state at a glance.

    Accepts raw dicts from ``state["user:positions"]`` — values may be
    full ``PositionThesis`` instances, their ``model_dump`` equivalents,
    or partial dicts from tests/early code paths.  Missing fields render
    gracefully.

    Per-row fields rendered
    -----------------------
    - Ticker symbol (header) + position-state tag
    - When owned: opened-at price + entry weight (frozen at decision time)
    - When owned AND a matching portfolio position is present: live close,
      live weight (drifts with price), and unrealised P&L as a signed %
      since entry.  Surfacing these closes the "lock-in-gains on a loss"
      hallucination from iter-3 (the strategist had no way to know whether
      a position was up or down without manual arithmetic).
    - Rationale (the agent's current view; mutable)
    - Thesis staleness in ticks

    Deliberately omits: ``horizon``, ``target_price``, ``stop_price`` —
    removed in iter-3.

    Parameters
    ----------
    positions:
        Mapping of ticker → thesis dict (or PositionThesis instance).
    current_tick_index:
        The current backtest tick index, read from
        ``state["user:current_tick_index"]``.  Used to compute staleness.
    portfolio:
        Optional live portfolio snapshot — when supplied, each
        ``[POSITION]`` row picks up its live price, current weight, and
        unrealised P&L from the matching ``Position``.  ``None`` skips the
        live overlay (the renderer still emits the entry block).

    Returns
    -------
    str
        Human-readable block for splicing into the strategist's prompt.
        Returns an empty-state sentinel when ``positions`` is empty.
    """
    if not positions:
        return "(Thesis book is empty — no views recorded yet.)"

    # ── Helper: format the open-date string ──────────────────────────────
    def _fmt_opened_at(raw_val) -> str:
        """Return a formatted open-date string from a datetime or ISO string."""
        if isinstance(raw_val, str):
            try:
                from datetime import datetime as _dt
                raw_val = _dt.fromisoformat(raw_val)
            except (TypeError, ValueError):
                raw_val = None

        if raw_val is not None and hasattr(raw_val, "strftime"):
            return raw_val.strftime("%Y-%m-%d %H:%M")
        return "(unknown date)"

    # ── Pre-compute NAV so per-ticker current weight is a single division.
    # NAV can be zero on cold-start fixtures — guard the division below.
    nav: float = portfolio.total_value if portfolio is not None else 0.0

    # ── Render one block per ticker, sorted for stable prompt diffs ──────
    lines: list[str] = ["## Thesis Book"]

    for ticker in sorted(positions.keys()):
        raw = positions[ticker]

        # Accept PositionThesis instances or plain dicts interchangeably.
        if hasattr(raw, "model_dump"):
            data: dict = raw.model_dump(mode="json")
        else:
            data = dict(raw)

        # Position state — the row owns a live position when the entry
        # fields are populated.  Fall back to ``opened_at`` as the
        # discriminator (mirrors the dispatcher's ``_has_live_position``).
        has_position = data.get("opened_at") is not None
        state_tag    = "[POSITION]" if has_position else "[NO POSITION]"

        rationale    = data.get("rationale") or "(no rationale recorded)"
        last_updated = data.get("thesis_last_updated_tick") or 0
        stale_ticks  = max(current_tick_index - last_updated, 0)

        block_lines: list[str] = [f"{ticker} {state_tag}"]

        if has_position:
            opened_price = data.get("opened_price") or 0.0
            opened_at    = _fmt_opened_at(data.get("opened_at"))
            entry_weight = data.get("weight")
            entry_w_str  = f"{entry_weight:.3f}" if entry_weight is not None else "—"
            block_lines.append(
                f"  Opened at ${opened_price:.2f} on {opened_at}  "
                f"(entry weight {entry_w_str})"
            )

            # Live overlay — only when the portfolio is supplied AND the ticker
            # is actually held (the thesis book can carry watched-only rows
            # whose ``[POSITION]`` tag predates an executed exit, so we don't
            # assume the position is still open).
            live_pos = portfolio.positions.get(ticker) if portfolio is not None else None
            if (
                live_pos is not None
                and live_pos.last_price > 0
                and live_pos.quantity > 0
            ):
                current_price = float(live_pos.last_price)
                current_w     = (live_pos.market_value / nav) if nav > 0 else 0.0

                # Unrealised P&L vs the avg-cost basis (volume-weighted across
                # all fills on this position) — more accurate than the thesis
                # opened_price when the position has been added to.
                if live_pos.avg_cost > 0:
                    unrealised_pct = (current_price / live_pos.avg_cost - 1.0) * 100.0
                    pnl_sign       = "+" if unrealised_pct >= 0 else ""
                    pnl_str        = f"{pnl_sign}{unrealised_pct:.2f}%"
                else:
                    pnl_str = "n/a"

                block_lines.append(
                    f"  Now ${current_price:.2f}  ({pnl_str})  "
                    f"current weight {current_w:.3f}"
                )

        block_lines.append(f"  Rationale:  {rationale}")

        block_lines.append(
            f"  Thesis staleness:  {stale_ticks} ticks since last update"
        )

        # Blank line between ticker blocks for legibility.
        lines.append("\n".join(block_lines))

    return "\n\n".join(lines)


def _render_recent_trades(closed_log: list[dict]) -> str:
    """Render the rolling closed-trade log as a compact text block.

    Parameters
    ----------
    closed_log:
        The list maintained by ``ExecutorAgent`` under
        ``state["user:closed_trades_log"]``.  Each entry has keys
        ``ticker``, ``closed_at``, ``pnl_pct``, ``holding_hours``,
        ``close_reason``.  May be empty.

    Returns
    -------
    str
        One line per trade (last 8 only), or a single explicit
        empty-state line when no trades have closed yet this run.
    """
    if not closed_log:
        return "(No closed positions yet this run.)"

    lines: list[str] = []
    for t in closed_log[-8:]:
        lines.append(
            f"  {t['ticker']:<6} {t['pnl_pct']:+6.2f}%  "
            f"held {t['holding_hours']}h  "
            f"closed: {t['close_reason'] or '(no reason given)'}"
        )
    return "\n".join(lines)
