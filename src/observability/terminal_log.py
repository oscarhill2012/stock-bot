"""Terminal logging for the StockBot tick pipeline (live + backtest).

Installs a single stderr handler that emits human-readable banners and per-LLM
call rows on the ``stockbot.tick`` logger, while silencing the chatty ADK
framework loggers that produce unhelpful "Sending out request" / "Response
received" noise.

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
- ``observability/terminal_log.py::make_observability_callbacks`` — one
  table row per completed LLM call.
"""
from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable

# The special logger name whose records are printed verbatim (no timestamp
# prefix).  Any other logger gets the standard ``%(asctime)s …`` format.
_TICK_LOGGER = "stockbot.tick"

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
    """Build ``(before_model_callback, after_model_callback)`` that log one table row per LLM call.

    The ``before_cb`` stamps a high-resolution start time into session state
    via ``callback_context.state``.  The ``after_cb`` reads it, computes the
    elapsed time, extracts token counts from ``llm_response.usage_metadata``,
    and emits one INFO line on the ``stockbot.tick`` logger.

    The two callbacks are designed to be chained with the existing cache
    callbacks via ``agents.analysts._common._chain_before`` /
    ``_chain_after``.  The cache ``before`` callback short-circuits on a cache
    hit (returns a non-None ``LlmResponse``); in that case ADK **does not**
    call the after-model chain at all.  To emit a ``(cached)`` marker for
    cache hits, the caller should detect the cache hit itself — this factory
    emits a ``(cached)`` line via a separate mechanism: see the ``before_cb``
    docstring.

    Parameters
    ----------
    analyst:
        Short analyst name, e.g. ``"news"`` or ``"fundamental"``.  Used in
        the state key so simultaneous analysts don't collide.
    ticker:
        The ticker symbol this branch is bound to (e.g. ``"AAPL"``).
    ticker_index:
        1-based position of this ticker in the watchlist (e.g. ``1`` for the
        first ticker).
    ticker_count:
        Total number of tickers in the watchlist.
    model_name:
        The model identifier string, e.g. ``"gemini-2.5-flash-lite"``.
        Included in the progress row when this is the first ticker.

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

    # Logger that the formatter treats as verbatim (no timestamp prefix).
    _tick_log = logging.getLogger(_TICK_LOGGER)

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
        """Emit one INFO table row on ``stockbot.tick``.

        Reads the start timestamp written by ``before_cb``, computes elapsed
        time, extracts token counts from ``llm_response.usage_metadata``
        (defensive — any field can be ``None`` in older ADK versions or when
        the LLM returns an error), and emits a formatted row.

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
        # Recover start timestamp (defensive — may be absent if the state key
        # was never written, e.g. during testing with a synthetic state dict).
        start = callback_context.state.get(_start_key)
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

        # Determine the status symbol.  We can't cleanly detect mid-flight
        # retry attempts here (the RetryingAgentWrapper retries before the
        # after-model chain fires), so we emit ✓ for every successful call
        # that reaches this hook.  The IsolatedFailureWrapper logs branch_failed
        # separately for complete failures.
        status = "✓"

        # Format token columns — show prompt= and out= only when we have data.
        if prompt_tokens is not None or candidate_tokens is not None:
            tok_str = (
                f"prompt={format_tokens(prompt_tokens)}  "
                f"out={format_tokens(candidate_tokens)}"
            )
        else:
            tok_str = ""

        lat_str = format_latency(elapsed)

        # Pad ticker to 5 characters for alignment.
        ticker_col = f"{ticker:<5}"

        row = (
            f"    {ticker_index:>2}/{ticker_count}  {ticker_col}  "
            f"{lat_str}   {tok_str}  {status}"
        )
        _tick_log.info(row)

        return None

    return before_cb, after_cb


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
    """Emit the per-analyst totals summary line.

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
