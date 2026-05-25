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

def setup_terminal_logging(
    level: int = logging.INFO,
    *,
    mode: str = "minimal",
) -> None:
    """Install a stderr handler and (optionally) silence ADK's chatty INFO loggers.

    Safe to call multiple times — subsequent calls are no-ops because the
    root logger already has the handler attached.

    Parameters
    ----------
    level:
        The minimum log level for the stderr handler.  Defaults to
        ``logging.INFO``.  Pass ``logging.DEBUG`` to see DEBUG records
        (note: ADK loggers are clamped to WARNING when ``mode`` is
        ``"minimal"`` or ``"info"`` regardless of this setting).
    mode:
        Verbosity profile for the terminal handler:

        - ``"minimal"`` (default): allowlist filter passes only
          ``stockbot.tick`` records (tick banners + analyst summary rows)
          plus any record at WARNING or above.  ADK loggers clamped to
          WARNING.  Cleanest terminal.
        - ``"info"``: drop the allowlist filter so cache callbacks,
          ``agents.isolated_failure`` WARNINGs, and other agent INFO
          records reach the terminal.  ADK loggers still clamped to
          WARNING so request/response chatter stays suppressed.
        - ``"debug"``: drop the filter AND do not clamp ADK loggers.
          Full firehose — every ADK request/response line, every cache
          hit/miss.  Use sparingly.
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

    # Allowlist filter — the terminal stays minimal: tick banners and analyst
    # summary rows (both on ``stockbot.tick``) plus any record at WARNING+ from
    # any logger so real errors stay visible.  Everything else (cache callbacks
    # INFO, ADK chatter, ``agents.isolated_failure`` per-branch WARNINGs that
    # are already counted in the "N failed" summary column, and per-attempt
    # ``agents.llm_retry`` WARNINGs that are already counted in the summary's
    # retry column) is dropped on the terminal but still reaches the buffered
    # ``runs/<id>/obs/logs/<tick>.json`` capture because the underlying
    # loggers and root level stay free.
    def _terminal_filter(record: logging.LogRecord) -> bool:
        """Return True if the record should be shown on the terminal.

        Two-rule allowlist: anything on the ``stockbot.tick`` logger (our own
        framed output), or any record at WARNING or above from any logger —
        with two suppressions.  ``agents.isolated_failure`` WARNINGs and the
        per-attempt ``llm_retry_attempt`` WARNINGs on ``agents.llm_retry`` are
        both already aggregated into the per-analyst summary row (as the
        ``N failed`` column and the ``retries=…`` column respectively), so
        repeating their detail on the terminal would just be noise.  The
        terminal-exhaustion ``llm_retry_exhausted`` ERROR is *not* suppressed
        — when a class actually runs out of attempts the operator should see
        the stacktrace, not just a count.
        """
        if record.name == _TICK_LOGGER:
            return True
        if record.name == "agents.isolated_failure":
            # Per-branch failure noise is already aggregated into the analyst
            # summary row's ``N failed`` column.  Drop from terminal.
            return False
        if record.name == "agents.llm_retry" and getattr(record, "kind", None) == "llm_retry_attempt":
            # Per-attempt retry chatter (429s, pydantic ValidationError detail,
            # timeout stacktraces) is already counted in the summary row's
            # ``retries=…`` column.  Drop from terminal; obs/logs still has it.
            return False
        return record.levelno >= logging.WARNING

    # The allowlist filter is only attached in ``minimal`` mode.  In
    # ``info`` / ``debug`` modes every record at ``level`` or above reaches
    # the terminal.
    if mode == "minimal":
        handler.addFilter(_terminal_filter)
    root.addHandler(handler)

    # Silence the ADK framework loggers in ``minimal`` and ``info`` modes —
    # they produce a pair of INFO lines per LLM call ("Sending out request"
    # / "Response received") which drown out our structured output with no
    # useful information.  We clamp the logger (not just the handler filter)
    # because the obs/ buffered capture also doesn't want this volume —
    # ADK request/response details are surfaced via the structured callback
    # path instead.  ``debug`` mode leaves them at their default INFO level.
    if mode in ("minimal", "info"):
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
        text = f"{int(k)}k" if k == int(k) else f"{k:.1f}k"
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
    and writes a single record to a *per-ticker* state key.

    The per-call detail is *also* emitted at DEBUG level on ``stockbot.tick.calls``
    so the buffered obs/ capture still gets it.  The terminal stderr handler
    is clamped to INFO, so the DEBUG records do not appear on screen.

    The record is stored at ``state["temp:_obs_<analyst>_call_<TICKER>"]``.
    The ``temp:`` prefix ensures ADK strips it at the invocation boundary so
    stale records from a previous tick never bleed into the next.

    Why per-ticker keys (not a shared list)
    --------------------------------------
    The original design used one shared list key per analyst
    (``temp:_obs_<analyst>_calls``) that every branch appended to.  Under
    ADK's ``ParallelAgent`` fan-out that pattern is a textbook
    read-modify-write race: each sibling branch reads a snapshot of the
    list, appends its own record, and writes the list back as part of its
    state_delta.  ADK merges sibling state_deltas with last-writer-wins
    semantics on a given key, so

      - records from "losing" branches silently vanish → false ``N
        failed`` count in the analyst summary row (e.g. ``16/20 ✓ 4
        failed`` when in fact all 20 verdicts landed); and
      - on retry, a discarded buffered event's append can occasionally
        re-surface when the merge picks a stale snapshot → false
        over-count (e.g. ``22/20 ✓``).

    Disjoint per-ticker keys eliminate both: there is at most one writer
    per key and exactly one record per ticker.  The joiner aggregates by
    iterating the watchlist and reading each ticker's key.

    After all per-ticker LLM branches for an analyst finish, the joiner
    (or strategist enricher) collects the per-ticker records and passes
    them to ``emit_analyst_summary`` to produce one tidy terminal row.

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

    # Per-ticker state key — each branch writes its single record to its own
    # key, so there is no race when sibling branches run in parallel.  The
    # joiner aggregates by iterating the watchlist and reading each ticker's
    # key.  See the function docstring for the full rationale.
    _call_key = f"temp:_obs_{analyst}_call_{ticker}"

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

        # Write this branch's single record to its disjoint per-ticker key.
        # No read-modify-write — each branch owns its own key, so there is
        # nothing to race on under parallel fan-out.  The joiner aggregates
        # by iterating the watchlist after all branches have completed.
        record: dict = {
            "ticker":           ticker,
            "elapsed":          elapsed,
            "prompt_tokens":    prompt_tokens,
            "candidate_tokens": candidate_tokens,
            "ok":               True,
        }
        state[_call_key] = record

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
    calls:        list[dict],
    ticker_count: int,
    retries:      dict[str, int] | None = None,
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
        List of per-call records.  The joiner builds this list by iterating
        the watchlist and reading each ticker's per-ticker scalar key
        ``state["temp:_obs_<analyst>_call_<TICKER>"]`` (written by
        ``make_observability_callbacks`` on LLM success and by
        ``cache_callbacks._before`` on cache hit).  Each record is a dict
        with keys ``ticker``, ``elapsed``, ``prompt_tokens``,
        ``candidate_tokens``, and ``ok``.  Pass an empty list when no
        branches completed (all failed).
    ticker_count:
        Total number of tickers that were *attempted* for this analyst.  Used
        to compute the failed count: ``failed = ticker_count - len(calls)``.
    retries:
        Optional per-tick retry-class counter dict, written by
        :class:`agents.llm_retry.RetryingAgentWrapper` to a per-analyst
        session-state key.  When non-empty, a ``· retries
        <class>×<n>`` suffix is appended to the summary row for each
        non-zero class.  Class order in the suffix is fixed:
        ``rate_limit``, ``timeout``, ``schema``.

    Returns
    -------
    None
    """
    tick_log = logging.getLogger(_TICK_LOGGER)

    succeeded  = len(calls)
    failed     = max(0, ticker_count - succeeded)
    ok_marker  = "✓" if not failed else "✓"  # always ✓ for completed ones

    # ── Cache-hit count ───────────────────────────────────────────────────────
    # Cache-hit records are appended by ``cache_callbacks._before`` when the
    # report cache short-circuits the LLM call.  They are real successes (the
    # verdict is valid; the LLM was just skipped), so they count toward
    # ``succeeded`` — but they carry no latency or token data, so reporting
    # them inside the latency/token statistics would be misleading.
    cached_count = sum(1 for r in calls if r.get("cache_hit"))
    fresh_count  = succeeded - cached_count
    all_cached   = succeeded > 0 and cached_count == succeeded

    # ── Token totals ─────────────────────────────────────────────────────────
    total_prompt    = sum(r.get("prompt_tokens")    or 0 for r in calls)
    total_candidate = sum(r.get("candidate_tokens") or 0 for r in calls)
    total_tokens    = total_prompt + total_candidate

    # ── Latency statistics ────────────────────────────────────────────────────
    # Cached records have ``elapsed=None`` so they naturally fall out of the
    # latency aggregation — only fresh LLM calls contribute timing data.
    latencies = [r["elapsed"] for r in calls if r.get("elapsed") is not None]

    # ── Build the row ─────────────────────────────────────────────────────────
    # Label column — fixed width so multiple analyst rows align vertically.
    label_col = f"{analyst_label + ':':<14}"

    # Count column — "succeeded/total ✓".
    count_col = f"{succeeded}/{ticker_count} {ok_marker}"

    # Failure annotation (omitted when there are none).
    fail_str = f"  {failed} failed" if failed else ""

    # Cache annotation — surfaced before latency so the operator sees at a
    # glance that timing/tokens are absent because the work was served from
    # the report cache, not because branches silently failed.
    if all_cached:
        cache_str = " (cached)"
    elif cached_count:
        cache_str = f" · {cached_count} cached"
    else:
        cache_str = ""

    if ticker_count == 1:
        # Singleton path — strategist or any analyst with only one ticker.
        # Shape: "strategist:     1/1  ✓  · 2.1s · 8.4k tok"
        # All-cached singleton: "fundamental:     1/1  ✓ (cached) · 0 tok"
        if all_cached:
            row = (
                f"  {label_col} {count_col:>8}{fail_str}{cache_str}"
                f" · 0 tok"
            )
        else:
            lat_str = format_latency(latencies[0] if latencies else None).strip()
            tok_str = f"{total_tokens / 1000:.1f}k" if total_tokens else "0"

            row = (
                f"  {label_col} {count_col:>8}{fail_str}{cache_str}"
                f" · {lat_str} · {tok_str} tok"
            )

    else:
        # Multi-ticker path — news, fundamental.
        # Shape (mixed): "news:    20/20 ✓ · 15 cached · median 1.4s · max 2.8s · 47.2k tok total"
        # Shape (all cached): "fundamental:    20/20 ✓ (cached) · 0 tok total"
        # Shape (all failed): "news:    0/20 ✓ 20 failed · no timing data · 0 tok total"
        if all_cached:
            # All work served from cache — no latency block, no token total
            # noise.  The "(cached)" marker already conveys the full picture.
            row = (
                f"  {label_col} {count_col:>8}{fail_str}{cache_str}"
                f" · 0 tok total"
            )
        else:
            if latencies:
                med_lat = statistics.median(latencies)
                max_lat = max(latencies)
                lat_str = (
                    f"median {format_latency(med_lat).strip()}"
                    f" · max {format_latency(max_lat).strip()}"
                )
                # Mixed run — clarify which slice the latency reflects.
                if cached_count and fresh_count:
                    lat_str = f"{lat_str} (of {fresh_count} fresh)"
            else:
                lat_str = "no timing data"

            tok_str = f"{total_tokens / 1000:.1f}k" if total_tokens else "0"

            row = (
                f"  {label_col} {count_col:>8}{fail_str}{cache_str}"
                f" · {lat_str} · {tok_str} tok total"
            )

    # Per-tick retry-counter suffix.  Only non-zero classes render; the
    # fixed order (rate_limit, timeout, schema) matches the
    # _classify dispatcher's priority order and keeps row layout stable.
    if retries:

        retry_order = ("rate_limit", "timeout", "schema")
        parts       = [
            f"{cls}×{retries[cls]}"
            for cls in retry_order
            if retries.get(cls)                                # non-zero only
        ]

        if parts:
            row = f"{row} · retries {' '.join(parts)}"

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
