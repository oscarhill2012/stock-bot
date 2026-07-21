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

- The held-positions view shows the calendar date the thesis was last
  revised (``Thesis updated: YYYY-MM-DD``, sourced from
  ``thesis_last_updated_at``) and deliberately omits ``horizon``,
  ``target_price``, and ``stop_price`` — those fields were removed in
  iter-3.  This replaced the earlier tick-count staleness line (see the
  "now-anchor" note below) — the strategist gets one clock, not two.

Strategist "now"-anchor
-----------------------
The strategist prompt previously had no clock: today's date appeared
nowhere in it, and per-thesis freshness was shown only as a tick count.
Two additions give it a real calendar anchor:

- ``temp:current_date`` — the tick's ``as_of`` date (``YYYY-MM-DD``),
  injected near the top of the prompt (``## Current State``) so the model
  can reason about elapsed calendar time.
- The per-thesis ``Thesis updated: YYYY-MM-DD`` line above, sourced from
  ``PositionThesis.thesis_last_updated_at``.
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


def _count_live_positions(positions: dict) -> int:
    """Count thesis-book rows that describe a LIVE position, not the whole book.

    ``state["user:positions"]`` holds one row per ticker the agent has a view
    on — owned or not (see ``position_thesis.py``).  A row whose ``opened_at``
    is populated describes a live position; a row whose ``opened_at`` is
    ``None`` is a watched-only view with no capital behind it.  This mirrors
    the exact discriminator ``_render_positions_shim`` uses for its
    ``[POSITION]`` / ``[NO POSITION]`` tags, so the count returned here always
    agrees with the number of ``[POSITION]`` tags the model sees in the
    rendered Thesis Book.

    Args:
        positions: Mapping of ticker -> thesis dict OR ``PositionThesis``
            instance.  Both are tolerated (dicts arrive from JSON-serialised
            session state; instances arrive from in-process test fixtures).

    Returns:
        int: The number of rows with a live position (``opened_at is not
            None``).  Zero for an empty or all-watched-only book.
    """
    count = 0

    for raw in positions.values():
        # Accept PositionThesis instances or plain dicts interchangeably —
        # mirrors the normalisation done in _render_positions_shim below.
        if hasattr(raw, "model_dump"):
            data: dict = raw.model_dump(mode="json")
        else:
            data = dict(raw)

        if data.get("opened_at") is not None:
            count += 1

    return count


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
          showing rationale, opened-at, current price/weight/P&L, and the
          calendar date the thesis was last updated.  Intentionally omits
          ``horizon``, ``target_price``, ``stop_price`` (removed in iter-3).
        - ``temp:deployment_readout`` — a one-line live summary of the current
          invested fraction, positioned inside ``## Deployment posture`` so the
          model sees its actual exposure right next to the 70–95% target band.
        - ``temp:portfolio_summary`` — a one-line cash/NAV/position-count
          summary rendered in ``## Current State``, replacing the old bare
          ``{portfolio}`` placeholder that dumped a raw dict repr (F7).

        Separating the pure computation from the ADK plumbing in
        ``_run_async_impl`` lets unit tests call ``render()`` directly without
        constructing a fake ``InvocationContext``.

        Args:
            state: ADK session-state dict / proxy.  Reads the following keys:
                ``user:active_stances_initialised`` (bool, defaults to False),
                ``user:positions`` (dict[ticker, thesis-dict], defaults to {}),
                ``user:current_tick_index`` (int, defaults to 0),
                ``portfolio`` (Portfolio dump, defaults to empty) — sourced so
                the held-view can show live price/weight/P&L per position, and
                the deployment-readout can sum current weights.

        Returns:
            dict with keys ``temp:first_tick_flag``, ``temp:first_tick_preamble``,
            ``temp:held_positions_view``, ``temp:deployment_readout``, and
            ``temp:portfolio_summary``.
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

        # ── Lightweight held-positions view with per-thesis last-updated date ──
        # A-014: read only the canonical user-namespaced key.  The
        # executor's bridge (temp:executor_positions_bridge) is
        # executor-internal and must never leak into the strategist's
        # held-view.
        positions = state.get("user:positions") or {}

        # Portfolio carries live ``last_price`` per held ticker and the cash
        # balance — feed it through so the thesis-book renderer can compute
        # current weight and unrealised P&L without needing extra state keys.
        portfolio = Portfolio.from_state_value(state.get("portfolio"))

        held_view = _render_positions_shim(
            positions,
            portfolio = portfolio,
        )

        # ── Live deployment readout ────────────────────────────────────────
        # Gives the model an explicit, computed signal about where it sits
        # relative to the 70–95% target band.  Without this the model cannot
        # tell it is under-deployed; it can only read the target prose and
        # try to infer its current exposure from the thesis-book weight lines.
        #
        # IMPORTANT: the band constants below must stay in sync with the prose
        # in the ``## Deployment posture`` section of prompts.py.  They are
        # intentionally NOT sourced from config — the prompt already hardcodes
        # 70–95% as prose, and introducing a new config value for this shim
        # would add indirection without any operational benefit.
        deployment_readout = _render_deployment_readout(portfolio)

        # ── Portfolio summary line (F7 prompt-hygiene cut) ────────────────
        # Replaces the old bare ``{portfolio}`` ADK placeholder, which
        # resolved to a raw ``Portfolio.model_dump()`` dict repr — 15-
        # significant-figure floats and all — dumped straight into the
        # prompt.  This one-line summary gives the model the plain cash/NAV/
        # position-count numbers without ever exposing a dict repr.
        portfolio_summary = _render_portfolio_summary(portfolio)

        return {
            "temp:first_tick_flag":      first_tick_flag,
            "temp:first_tick_preamble":  first_tick_preamble,
            "temp:held_positions_view":  held_view,
            "temp:deployment_readout":   deployment_readout,
            "temp:portfolio_summary":    portfolio_summary,
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
        thesis-book renderer that shows position state, rationale, and
        the thesis's last-updated calendar date, and omits horizon/target/stop.

        Also writes ``temp:current_date`` — the tick's ``as_of`` date as a
        plain ``YYYY-MM-DD`` string, giving the strategist prompt a "now"
        anchor near the top (``## Current State``).

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
            # N must count only rows with a live position, not every row in
            # the thesis book — the book also carries watched-only rows
            # (opened_at is None), and INCREMENTAL_MODE_TEMPLATE explicitly
            # says "you hold {N} live position(s)".  See _count_live_positions.
            mode_text = INCREMENTAL_MODE_TEMPLATE.format(N=_count_live_positions(positions))

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

        # ── Yield exactly one Event carrying all required keys ────────────
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "temp:strategist_mode":         mode_text,
                # Change 1 — "now"-anchor: the tick's as_of date, plain
                # YYYY-MM-DD, injected near the top of the prompt (##
                # Current State) so the strategist can reason about
                # elapsed calendar time.  Reuses the same ``recorded_at``
                # resolved above via resolve_as_of (backtest as_of >
                # recorded_at > wall-clock) for a single source of truth.
                "temp:current_date":            recorded_at.strftime("%Y-%m-%d"),
                # Held-positions view, first-tick flag, and first-tick preamble
                # from the pure render() helper — separated so unit tests can
                # call render() directly without a fake InvocationContext.
                "temp:held_positions_view":     pure_keys["temp:held_positions_view"],
                "temp:first_tick_flag":         pure_keys["temp:first_tick_flag"],
                # First-tick-only guidance block — full text on tick 0 (empty
                # thesis book; model must populate), empty string on all
                # subsequent ticks (Deployment posture + Mode already cover it).
                "temp:first_tick_preamble":     pure_keys["temp:first_tick_preamble"],
                # Live deployment readout — one-line summary of current invested
                # fraction vs the 70–95% target band, placed in ## Deployment
                # posture so the model sees its actual exposure alongside the
                # target guidance.
                "temp:deployment_readout":      pure_keys["temp:deployment_readout"],
                # F7: clean cash/NAV/position-count line replacing the raw
                # {portfolio} dict dump in ## Current State.
                "temp:portfolio_summary":       pure_keys["temp:portfolio_summary"],
                "temp:ticker_evidence":         ticker_evidence_rendered,
                "temp:ticker_evidence_objects": ticker_evidence_objects,
                # Schema-error feedback slot — empty on the first attempt;
                # the RetryingAgentWrapper overwrites it with the formatted
                # Pydantic validation error before each schema retry so the
                # LLM sees what it got wrong on the previous turn.  The
                # prompt template renders the placeholder verbatim; an empty
                # string yields a blank line that LLMs ignore.
                "temp:_last_schema_error":      "",
            }),
        )


def _render_deployment_readout(portfolio: Portfolio) -> str:
    """Produce a one-line live deployment summary for the strategist prompt.

    Computes the fraction of portfolio NAV currently invested in positions
    (i.e. sum of all position market values divided by total NAV) and
    formats a plain-English readout that explicitly names the 70–95% target
    band and whether the portfolio is below, within, or above it.

    The directional cue (BELOW / WITHIN / ABOVE) is on the same line so
    the model always knows where it stands — no arithmetic needed.

    NOTE: the 70/95 band constants here intentionally mirror the prose in
    ``## Deployment posture`` in prompts.py.  If you ever change the target
    band in the prompt, update these constants too.

    Args:
        portfolio:
            Live ``Portfolio`` snapshot.  An empty portfolio (no positions,
            pure cash) degrades gracefully to a "0% invested / 100% cash"
            readout rather than raising.

    Returns:
        str
            A single-line readout, e.g.:
            ``"Capital deployed: 51% invested across 6 positions, 49% idle cash.
            Target band: 70–95%. You are 19pp BELOW the band — idle cash is
            bearish drag."``
    """
    # ── Band constants — MUST match the prose in prompts.py ## Deployment posture.
    _BAND_LOW_PCT  = 70   # lower edge of the target band (%)
    _BAND_HIGH_PCT = 95   # upper edge of the target band (%)

    weights = portfolio.current_weights()

    # Sum of all position weights = invested fraction (remainder is cash).
    invested_frac: float = sum(weights.values())
    invested_pct:  int   = round(invested_frac * 100)
    cash_pct:      int   = 100 - invested_pct
    n_positions:   int   = len(weights)

    # Positions noun (singular vs plural) for grammatical English.
    pos_noun = "position" if n_positions == 1 else "positions"

    # ── Directional cue relative to the target band ───────────────────────
    if invested_pct < _BAND_LOW_PCT:
        gap_pp = _BAND_LOW_PCT - invested_pct
        direction_cue = (
            f"You are {gap_pp}pp BELOW the band — idle cash is bearish drag."
        )
    elif invested_pct > _BAND_HIGH_PCT:
        gap_pp = invested_pct - _BAND_HIGH_PCT
        direction_cue = (
            f"You are {gap_pp}pp ABOVE the band — trim the lowest-conviction positions."
        )
    else:
        direction_cue = "You are WITHIN the target band."

    return (
        f"Capital deployed: {invested_pct}% invested across "
        f"{n_positions} {pos_noun}, {cash_pct}% idle cash. "
        f"Target band: {_BAND_LOW_PCT}–{_BAND_HIGH_PCT}%. "
        f"{direction_cue}"
    )


def _render_portfolio_summary(portfolio: Portfolio) -> str:
    """Produce a clean one-line cash/NAV/position-count summary (F7).

    Replaces the old bare ``{portfolio}`` ADK placeholder, which resolved to
    a raw ``Portfolio.model_dump()`` dict repr — 15-significant-figure floats
    and all — dumped straight into the strategist prompt.  Per-position
    weight/P&L detail already lives in the Thesis Book, and the invested
    fraction vs the 70-95% target band already lives in
    ``_render_deployment_readout``; this line only needs to give the model
    the raw cash/NAV figures those percentages are computed from, rounded to
    whole dollars so no long float tail can leak into the prompt.

    Args:
        portfolio:
            Live ``Portfolio`` snapshot.  An empty (cash-only) portfolio
            degrades gracefully — ``n_positions`` is simply 0.

    Returns:
        str
            A single-line summary, e.g.
            ``"Cash: $48,120 | NAV: $100,240 | 6 position(s)"``.
    """
    cash_rounded = round(portfolio.cash)
    nav_rounded  = round(portfolio.total_value)
    n_positions  = len(portfolio.positions)

    return (
        f"Cash: ${cash_rounded:,} | NAV: ${nav_rounded:,} | "
        f"{n_positions} position(s)"
    )


def _render_positions_shim(
    positions: dict,
    *,
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
    - Thesis last-updated date (calendar date the thesis was last
      revised via ``buy``/``update`` — gives the strategist a real
      clock instead of a bare tick count)

    Deliberately omits: ``horizon``, ``target_price``, ``stop_price`` —
    removed in iter-3.

    Parameters
    ----------
    positions:
        Mapping of ticker → thesis dict (or PositionThesis instance).
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

    # ── Helper: format the thesis-last-updated date (date only, no time) ──
    # Gives the strategist a real calendar anchor for "how long ago did I
    # last revise this view" instead of a bare tick count — replaces the
    # old "N ticks since last update" staleness line entirely.
    def _fmt_updated_date(raw_val) -> str:
        """Return a plain YYYY-MM-DD date string from a datetime or ISO string.

        ``None`` covers thesis rows written before ``thesis_last_updated_at``
        existed (backward-compat default) — rendered as an explicit sentinel
        rather than a fabricated date.
        """
        if isinstance(raw_val, str):
            try:
                from datetime import datetime as _dt
                raw_val = _dt.fromisoformat(raw_val)
            except (TypeError, ValueError):
                raw_val = None

        if raw_val is not None and hasattr(raw_val, "strftime"):
            return raw_val.strftime("%Y-%m-%d")
        return "(unknown)"

    # ── Pre-compute NAV so per-ticker current weight is a single division.
    # NAV can be zero on cold-start fixtures — guard the division below.
    nav: float = portfolio.total_value if portfolio is not None else 0.0

    # ── Render one block per ticker, sorted for stable prompt diffs ──────
    # Header is NOT emitted here — the strategist template (prompts.py)
    # already prints "## Thesis Book" immediately above
    # {temp:held_positions_view}; emitting it here too would double it up
    # back-to-back in the assembled prompt.
    lines: list[str] = []

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

        # Calendar date of the thesis's last revision (buy/update) — replaces
        # the old tick-count staleness line so the strategist can reason
        # about elapsed calendar time rather than an opaque tick counter.
        updated_date = _fmt_updated_date(data.get("thesis_last_updated_at"))
        block_lines.append(f"  Thesis updated: {updated_date}")

        # Blank line between ticker blocks for legibility.
        lines.append("\n".join(block_lines))

    return "\n\n".join(lines)
