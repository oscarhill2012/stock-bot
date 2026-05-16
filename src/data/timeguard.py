"""Centralised resolution of ``as_of`` historical clock values.

Every code path that needs an ``as_of`` datetime should route its
``None``-handling through :func:`resolve_as_of` rather than substituting
``datetime.now(tz=UTC)`` inline.

The helper has two responsibilities:

1. **Strict-mode enforcement.** When the environment variable
   ``STOCKBOT_STRICT_AS_OF=1`` is set (done by backtest entrypoints) a
   missing ``as_of`` is treated as a programming error and surfaced as
   :class:`AsOfRequiredError` rather than silently leaking wall-clock
   time into the dataset.

2. **Explicit live-mode opt-in.** Even with strict mode off, callers
   must explicitly opt into wall-clock fallback via
   ``allow_wallclock=True``.  This documents the intent at each call
   site — anywhere the wall clock is acceptable, the code says so.

Live entrypoints (``orchestrator/tick.py`` and any executor invocation
outside the backtest call tree) pass ``allow_wallclock=True``.
Everything inside the backtest call tree leaves it at the default
``False`` — together with the ``STOCKBOT_STRICT_AS_OF`` env var this
gives a belt-and-braces guarantee that a missing ``as_of`` cannot be
silently fabricated during a backtest.
"""
from __future__ import annotations

import os
import threading
from datetime import UTC, datetime


class AsOfRequiredError(RuntimeError):
    """Raised when a historical ``as_of`` is required but was not supplied.

    Surfaces when ``STOCKBOT_STRICT_AS_OF=1`` is set or the caller passed
    ``allow_wallclock=False`` (the default).  The error message embeds the
    call-site label so the reviewer can pinpoint which layer was missing
    its plumbing.
    """


_STRICT_ENV_VAR  = "STOCKBOT_STRICT_AS_OF"
_STRICT_ENABLED  = "1"


# ── per-tick wall-clock fallback counter ──────────────────────────────────────
#
# When a backtest tick runs with strict mode OFF, ``resolve_as_of`` may return
# the wall clock as a defensive fallback.  Phase 6 audit telemetry needs to
# know whether *any* fallback fired during the tick so it can surface the
# ``wall_clock_fallback_fired`` tripwire.  We use a thread-local because the
# ADK invocation runs on a single asyncio loop within one thread per backtest
# run; the counter is read+reset by the driver immediately after each tick.

_FALLBACK_STATE = threading.local()


def _get_counter() -> int:
    """Return the current thread-local fallback count (default ``0``)."""

    return getattr(_FALLBACK_STATE, "count", 0)


def _set_counter(value: int) -> None:
    """Overwrite the thread-local fallback count."""

    _FALLBACK_STATE.count = value


def drain_wallclock_fallback_count() -> int:
    """Return the current count of wall-clock fallbacks and reset to zero.

    The backtest driver calls this once per tick.  Returns ``0`` on first
    use of the current thread.
    """

    count = _get_counter()
    _set_counter(0)
    return count


def resolve_as_of(
    candidate: datetime | None,
    *,
    allow_wallclock: bool = False,
    site: str = "<unknown>",
) -> datetime:
    """Return ``candidate`` if supplied; otherwise fall back or raise.

    Parameters
    ----------
    candidate:
        The ``as_of`` value provided by the caller.  May be ``None`` if
        the caller did not propagate one through.
    allow_wallclock:
        When ``True`` *and* strict mode is off, ``datetime.now(tz=UTC)``
        is returned in place of a missing candidate.  When ``False`` (the
        default) the helper always raises on a missing candidate.  Live
        entrypoints set this to ``True``; backtest code leaves it at the
        default.
    site:
        Short label naming the call site (e.g. ``"aggregator"``,
        ``"news_fetch"``).  Embedded in the error message so a strict-mode
        failure tells the reviewer which layer was missing its plumbing.

    Returns
    -------
    datetime
        A timezone-aware datetime — either ``candidate`` (when supplied)
        or the wall-clock fallback (live mode only).

    Raises
    ------
    AsOfRequiredError
        When ``candidate is None`` *and* either strict mode is enabled
        (``STOCKBOT_STRICT_AS_OF=1``) or ``allow_wallclock=False``.
    """
    # Happy path: the caller supplied an explicit timestamp.
    if candidate is not None:
        return candidate

    # Strict mode is an absolute veto on wall-clock substitution.
    strict = os.environ.get(_STRICT_ENV_VAR) == _STRICT_ENABLED

    if strict or not allow_wallclock:
        raise AsOfRequiredError(
            f"as_of is required at site={site!r}; wall-clock fallback disabled "
            f"(strict_env={strict}, allow_wallclock={allow_wallclock})"
        )

    # Live path — caller has explicitly opted in.  Bump the per-tick counter
    # so the backtest driver can surface this on the audit tripwire.
    _set_counter(_get_counter() + 1)
    return datetime.now(tz=UTC)
