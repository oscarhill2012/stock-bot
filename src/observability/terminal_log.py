"""Terminal logging for the StockBot tick pipeline (live + backtest).

Installs a single stderr handler that emits human-readable banners and
per-analyst summary rows on the ``stockbot.tick`` logger, while silencing
the chatty ADK framework loggers that produce unhelpful "Sending out request"
/ "Response received" noise.

Used by both entrypoints:

- ``scripts/smoke_run.py``       — live tick loop
- ``scripts/backtest_run.py``    — backtest replay

Backtest callers should also bump the root logger to ``DEBUG`` after calling
``setup_terminal_logging`` so the buffered observability handlers can capture
DEBUG records into ``runs/<id>/obs/logs/<tick>.json``.  The stderr handler
this module installs stays at INFO regardless, so the terminal stays clean.

Usage (at the top of the entrypoint, before any agent code runs)::

    from observability.terminal_log import setup_terminal_logging
    setup_terminal_logging()

The ``stockbot.tick`` logger is then used by:

- ``orchestrator/tick.py`` — tick banners and summary lines.
- ``observability/terminal_log.py::make_observability_callbacks`` — appends
  per-call records to an accumulator in session state.
- ``observability/terminal_log.py::emit_analyst_summary`` — emits one
  tidy summary row per analyst per tick after the joiner/strategist finishes.

Per-call detail (latency, token counts) is also written at DEBUG level on
``stockbot.tick.calls`` so the buffered obs/ capture still gets it without
polluting the terminal (the stderr handler is clamped to INFO).
"""
from __future__ import annotations

import logging
import statistics
import sys
import time
from collections.abc import Callable

# The special logger name whose records are printed verbatim (no timestamp
# prefix).  Any other logger gets the standard ``%(asctime)s …`` format.
_TICK_LOGGER = "stockbot.tick"

# Separate logger for per-call detail — emits at DEBUG so the buffered
# obs/ capture sees it, but the stderr handler (INFO) does not print it.
_CALLS_LOGGER = "stockbot.tick.calls"

# Loggers from the ADK framework that are too chatty at INFO level.
_ADK_NOISY_LOGGERS: tuple[str, ...] = (
    "google_adk",
    "google.adk",
)


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------

class _TickFormatter(logging.Formatter):
    """Custom log formatter for the smoke-run terminal handler.

    Records from ``stockbot.tick`` are printed verbatim — no timestamp, no
    level prefix — because the tick banner/table rows carry their own
    human-readable framing.  All other records get the standard
    ``YYYY-MM-DD HH:MM:SS,mmm LEVEL name message`` format so framework noise
    (warnings, errors) is still identifiable.
    """

    _STD_FMT  = "%(asctime)s %(levelname)s %(name)s %(message)s"
    _STD_DATE = "%Y-%m-%d %H:%M:%S"

    def __init__(self) -> None:
        """Initialise the formatter with the standard format as a fallback."""
        super().__init__(fmt=self._STD_FMT, datefmt=self._STD_DATE)
        # A secondary formatter for non-tick records so we can apply the
        # standard template without re-implementing it.
        self._std = logging.Formatter(fmt=self._STD_FMT, datefmt=self._STD_DATE)

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record.

        Parameters
        ----------
        record:
            The log record to format.

        Returns
        -------
        str
            Verbatim ``record.getMessage()`` for ``stockbot.tick``; the
            standard formatted string for everything else.
        """
        if record.name == _TICK_LOGGER:
            # Verbatim — the message already contains all the framing we want.
            return record.getMessage()

        return self._std.format(record)


# ---------------------------------------------------------------------------
# Public setup function
# ---------------------------------------------------------------------------

def setup_terminal_logging(level: int = logging.INFO) -> None:
    """Install a stderr handler and silence ADK's chatty INFO loggers.

    Safe to call multiple times — subsequent calls are no-ops because the
    root logger already has the handler attached.

    Parameters
    ----------
    level:
        The minimum log level for the stderr handler.  Defaults to
        ``logging.INFO``.  Pass ``logging.DEBUG`` to see ADK debug output
        (note: ADK loggers are clamped to WARNING regardless of this setting).
    """
    root = logging.getLogger()

    # Idempotency guard — if we've already installed the handler, skip.
    # We identify our handler by the custom formatter class.
    for h in root.handlers:
        if isinstance(getattr(h, "formatter", None), _TickFormatter):
            return

    # Root logger must be at least at ``level`` so records flow through.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(_TickFormatter())
    root.addHandler(handler)

    # Silence the ADK framework loggers — they produce a pair of INFO lines
    # per LLM call ("Sending out request" / "Response received") which drown
    # out our structured output with no useful information.
    for name in _ADK_NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_tokens(n: int | None) -> str:
    """Format a token count as a compact k-suffix string.

    Always produces a fixed-width representation of up to 6 characters so
    table rows line up in a monospace terminal.

    Parameters
    ----------
    n:
        Token count, or ``None`` for a missing value.

    Returns
    -------
    str
        Examples: ``" 8.5k"``, ``" 168k"``, ``"  0.8k"``, ``"   0"``.
        Padded on the left to 6 characters.

    Examples
    --------
    >>> format_tokens(8500)
    '  8.5k'
    >>> format_tokens(168000)
    ' 168k'
    >>> format_tokens(0)
    '     0'
    """
    if n is None or n == 0:
        return f"{'0':>6}"

    if n >= 1000:
        # Express as Nk with one decimal place where the decimal is non-zero,
        # or as a plain integer k otherwise (e.g. 168k rather than 168.0k).
        k = n / 1000.0
        if k == int(k):
            text = f"{int(k)}k"
        else:
            text = f"{k:.1f}k"
    else:
        text = str(n)

    return f"{text:>6}"


def format_latency(seconds: float | None) -> str:
    """Format a duration in seconds as a fixed-width string.

    Parameters
    ----------
    seconds:
        Duration in seconds, or ``None`` for a missing value.

    Returns
    -------
    str
        Examples: ``" 4.12s"``, ``"12.34s"``.  Always 6 characters wide.

    Examples
    --------
    >>> format_latency(4.12)
    ' 4.12s'
    >>> format_latency(12.345)
    '12.35s'
    """
    if seconds is None:
        return " " * 6

    return f"{seconds:>5.2f}s"


# ---------------------------------------------------------------------------
# Observability callback factory
# ---------------------------------------------------------------------------

def make_observability_callbacks(
    *,
    analyst: str,
    ticker: str,
    ticker_index: int,
    ticker_count: int,
    model_name: str,
) -> tuple[Callable, Callable]:
    """Build ``(before_model_callback, after_model_callback)`` that accumulate per-call records.

    The ``before_cb`` stamps a high-resolution start time into session state
    via ``callback_context.state``.  The ``after_cb`` reads it, computes the
    elapsed time, extracts token counts from ``llm_response.usage_metadata``,
    and appends a record to the analyst's call accumulator list in session state.

    The per-call detail is *also* emitted at DEBUG level on ``stockbot.tick.calls``
    so the buffered obs/ capture still gets it.  The terminal stderr handler
    is clamped to INFO, so the DEBUG records do not appear on screen.

    The accumulator list is stored at ``state["temp:_obs_<analyst>_calls"]``.
    The ``temp:`` prefix ensures ADK strips it at the invocation boundary so
    stale records from a previous tick never bleed into the next.

    After all per-ticker LLM branches for an analyst finish, the joiner
    (or strategist) reads this accumulator and passes it to
    ``emit_analyst_summary`` to produce one tidy terminal row.

    The two callbacks are designed to be chained with the existing cache
    callbacks via ``agents.analysts._common._chain_before`` /
    ``_chain_after``.

    Parameters
    ----------
    analyst:
        Short analyst name, e.g. ``"news"`` or ``"fundamental"``.  Used in
        the state key so simultaneous analysts don't collide.
    ticker:
        The ticker symbol this branch is bound to (e.g. ``"AAPL"``).
    ticker_index:
        1-based position of this ticker in the watchlist (e.g. ``1`` for the
        first ticker).  Included in the DEBUG per-call record.
    ticker_count:
        Total number of tickers in the watchlist.  Included in the DEBUG
        per-call record.
    model_name:
        The model identifier string, e.g. ``"gemini-2.5-flash-lite"``.
        Included in the DEBUG per-call record.

    Returns
    -------
    tuple[Callable, Callable]
        ``(before_model_callback, after_model_callback)`` ready to be passed
        to ``_chain_before`` / ``_chain_after``.
    """
    # State key for the start timestamp — include analyst + ticker to prevent
    # key collisions when parallel branches run.  The ``temp:`` prefix ensures
    # ADK strips these ephemeral values at the invocation boundary.
    _start_key = f"temp:_llm_start_{analyst}_{ticker}"

    # State key for the per-analyst accumulator list.  All per-ticker branches
    # for the same analyst append to the same list; the joiner reads it once
    # all branches have completed.
    _accum_key = f"temp:_obs_{analyst}_calls"

    # Logger that the formatter treats as verbatim (no timestamp prefix).
    _tick_log   = logging.getLogger(_TICK_LOGGER)
    _calls_log  = logging.getLogger(_CALLS_LOGGER)

    def before_cb(callback_context, llm_request) -> None:
        """Stamp the high-resolution start time into session state.

        Does not return a value — the observability before-callback never
        short-circuits the LLM call.  The cache before-callback (run first
        in the chain) handles short-circuiting on cache hits.

        Parameters
        ----------
        callback_context:
            ADK callback context with mutable session state.
        llm_request:
            The pending LLM request (not inspected here).

        Returns
        -------
        None
            Always ``None`` — this hook never short-circuits.
        """
        callback_context.state[_start_key] = time.perf_counter()
        return None

    def after_cb(callback_context, llm_response) -> None:
        """Append a call record to the analyst accumulator; emit detail at DEBUG.

        Reads the start timestamp written by ``before_cb``, computes elapsed
        time, extracts token counts from ``llm_response.usage_metadata``
        (defensive — any field can be ``None`` in older ADK versions or when
        the LLM returns an error), and appends a structured record to the
        analyst accumulator list.

        Also emits a DEBUG-level line on ``stockbot.tick.calls`` so the
        buffered obs/ capture still captures per-call detail without it
        appearing on the terminal (stderr handler is clamped to INFO).

        Does NOT emit on ``stockbot.tick`` at INFO — that is handled once per
        analyst by ``emit_analyst_summary`` after all branches finish.

        Parameters
        ----------
        callback_context:
            ADK callback context with mutable session state.
        llm_response:
            The raw LLM response.  Token counts come from
            ``llm_response.usage_metadata.prompt_token_count`` and
            ``.candidates_token_count``.

        Returns
        -------
        None
            Always ``None`` — this hook is side-effect only.
        """
        state = callback_context.state

        # Recover start timestamp (defensive — may be absent if the state key
        # was never written, e.g. during testing with a synthetic state dict).
        start   = state.get(_start_key)
        elapsed = (time.perf_counter() - start) if start is not None else None

        # Extract token counts from usage_metadata.  Any attribute can be
        # None if the model didn't return billing info (e.g. on error).
        prompt_tokens    = None
        candidate_tokens = None

        try:
            meta = getattr(llm_response, "usage_metadata", None)
            if meta is not None:
                prompt_tokens    = getattr(meta, "prompt_token_count",     None)
                candidate_tokens = getattr(meta, "candidates_token_count", None)
        except Exception:
            # Defensive — never crash the pipeline on observability code.
            pass

        # Append a structured record to the analyst accumulator.  The joiner
        # reads this list once all per-ticker branches have completed and passes
        # it to ``emit_analyst_summary`` to build the terminal summary row.
        record: dict = {
            "ticker":           ticker,
            "elapsed":          elapsed,
            "prompt_tokens":    prompt_tokens,
            "candidate_tokens": candidate_tokens,
            "ok":               True,
        }

        # Safely initialise the accumulator if this is the first branch to finish.
        existing = state.get(_accum_key)
        if not isinstance(existing, list):
            existing = []
        existing.append(record)
        state[_accum_key] = existing

        # Emit per-call detail at DEBUG level on the calls sub-logger so the
        # buffered obs/ capture still has the fine-grained data.  The terminal
        # stderr handler (INFO) will not print this.
        lat_str = format_latency(elapsed)
        tok_str = (
            f"prompt={format_tokens(prompt_tokens)} "
            f"out={format_tokens(candidate_tokens)}"
            if (prompt_tokens is not None or candidate_tokens is not None)
            else ""
        )
        _calls_log.debug(
            "%s %s  %2d/%2d  %s  %s  ✓",
            analyst, ticker, ticker_index, ticker_count, lat_str, tok_str,
        )

        # Suppress the _tick_log INFO emission that existed in the old
        # per-call design — the terminal now sees only the summary row.
        return None

    return before_cb, after_cb


# ---------------------------------------------------------------------------
# Per-analyst summary emitter
# ---------------------------------------------------------------------------

def emit_analyst_summary(
    analyst_label: str,
    *,
    calls: list[dict],
    ticker_count: int,
) -> None:
    """Emit one tidy summary row per analyst per tick on ``stockbot.tick``.

    This replaces the old per-call INFO rows that became unreadable once
    parallelism made them arrive interleaved.  Call it from the analyst's
    joiner (or the strategist's validation callback) after all LLM branches
    for the analyst have completed.

    The row shape depends on how many tickers the analyst handled:

    - **Multi-ticker** (``ticker_count > 1``)::

        news:         18/20 ✓  2 failed · median 1.4s · max 2.8s · 47.2k tok total

    - **Singleton** (``ticker_count == 1``)::

        strategist:    1/1  ✓           · 2.1s · 8.4k tok

    Failed tickers are those in ``ticker_count`` but NOT represented in
    ``calls`` — i.e. their LLM branch crashed and the after-callback was never
    reached.  The caller is responsible for passing ``ticker_count`` equal to
    the number of tickers that were *attempted*, not just those that succeeded.

    Parameters
    ----------
    analyst_label:
        Human-readable analyst name used as the row label, e.g. ``"news"``,
        ``"fundamental"``, or ``"strategist"``.
    calls:
        List of per-call records accumulated by ``make_observability_callbacks``
        and stored at ``state["temp:_obs_<analyst>_calls"]``.  Each record is a
        dict with keys ``ticker``, ``elapsed``, ``prompt_tokens``,
        ``candidate_tokens``, and ``ok``.  Pass an empty list when no calls
        completed (all branches failed).
    ticker_count:
        Total number of tickers that were *attempted* for this analyst.  Used
        to compute the failed count: ``failed = ticker_count - len(calls)``.

    Returns
    -------
    None
    """
    tick_log = logging.getLogger(_TICK_LOGGER)

    succeeded  = len(calls)
    failed     = max(0, ticker_count - succeeded)
    ok_marker  = "✓" if not failed else "✓"  # always ✓ for completed ones

    # ── Token totals ─────────────────────────────────────────────────────────
    total_prompt    = sum(r.get("prompt_tokens")    or 0 for r in calls)
    total_candidate = sum(r.get("candidate_tokens") or 0 for r in calls)
    total_tokens    = total_prompt + total_candidate

    # ── Latency statistics ────────────────────────────────────────────────────
    latencies = [r["elapsed"] for r in calls if r.get("elapsed") is not None]

    # ── Build the row ─────────────────────────────────────────────────────────
    # Label column — fixed width so multiple analyst rows align vertically.
    label_col = f"{analyst_label + ':':<14}"

    # Count column — "succeeded/total ✓".
    count_col = f"{succeeded}/{ticker_count} {ok_marker}"

    # Failure annotation (omitted when there are none).
    fail_str = f"  {failed} failed" if failed else ""

    if ticker_count == 1:
        # Singleton path — strategist or any analyst with only one ticker.
        # Shape: "strategist:     1/1  ✓  · 2.1s · 8.4k tok"
        lat_str = format_latency(latencies[0] if latencies else None).strip()
        tok_str = f"{total_tokens / 1000:.1f}k" if total_tokens else "0"

        row = (
            f"  {label_col} {count_col:>8}{fail_str}"
            f" · {lat_str} · {tok_str} tok"
        )

    else:
        # Multi-ticker path — news, fundamental.
        # Shape: "news:         18/20 ✓  2 failed · median 1.4s · max 2.8s · 47.2k tok total"
        if latencies:
            med_lat = statistics.median(latencies)
            max_lat = max(latencies)
            lat_str = f"median {format_latency(med_lat).strip()} · max {format_latency(max_lat).strip()}"
        else:
            lat_str = "no timing data"

        tok_str = f"{total_tokens / 1000:.1f}k" if total_tokens else "0"

        row = (
            f"  {label_col} {count_col:>8}{fail_str}"
            f" · {lat_str} · {tok_str} tok total"
        )

    tick_log.info(row)


# ---------------------------------------------------------------------------
# Legacy compatibility shim — kept so existing callers don't break at import
# ---------------------------------------------------------------------------

def emit_analyst_totals(
    analyst_label: str,
    *,
    ticker_count: int,
    ok_count: int,
    failed_count: int,
    cached_count: int,
    wall_seconds: float,
    total_prompt_tokens: int,
    total_candidate_tokens: int,
) -> None:
    """Emit the per-analyst totals summary line (legacy signature).

    This function is retained for backwards compatibility only.  New code
    should call ``emit_analyst_summary`` directly.  The old wide signature
    is kept so that any caller that hasn't migrated yet does not crash at
    import or runtime.

    Parameters
    ----------
    analyst_label:
        Human-readable analyst name, e.g. ``"News"``.
    ticker_count:
        Total number of tickers processed.
    ok_count:
        Number of successful (non-failed) LLM calls.
    failed_count:
        Number of tickers for which the branch failed.
    cached_count:
        Number of tickers served from the report cache (no LLM call).
    wall_seconds:
        Wall-clock seconds from analyst start to finish.
    total_prompt_tokens:
        Sum of prompt token counts across all calls.
    total_candidate_tokens:
        Sum of candidate/output token counts across all calls.
    """
    tick_log = logging.getLogger(_TICK_LOGGER)

    tok_in  = format_tokens(total_prompt_tokens)
    tok_out = format_tokens(total_candidate_tokens)

    parts = [
        f"  {analyst_label} totals →",
        f"  {ticker_count} tickers",
        f"· {ok_count} ok",
    ]
    if cached_count:
        parts.append(f"· {cached_count} cached")
    if failed_count:
        parts.append(f"· {failed_count} failed")
    parts.append(f"· {wall_seconds:.1f}s wall")
    parts.append(f"· {tok_in.strip()} tok in / {tok_out.strip()} tok out")

    tick_log.info(" ".join(parts))


def emit_analyst_header(analyst_label: str, model_name: str) -> None:
    """Emit the section header line for one analyst phase.

    Call this immediately before the first per-ticker branch of a given
    analyst runs.

    Parameters
    ----------
    analyst_label:
        Human-readable analyst name, e.g. ``"News"`` or ``"Fundamental"``.
    model_name:
        The model identifier to display in the header line.
    """
    tick_log = logging.getLogger(_TICK_LOGGER)
    tick_log.info(f"  {analyst_label:<14} {model_name}")
