"""Per-run news last-fire store (Phase 14, Plan 3c, Task 9).

Records the most recent DIRECTIONAL news verdict (bullish/bearish) fired for
each ticker.  Task 10 will consume this store on subsequent ABSTAIN
(neutral/no-data) ticks so the strategist can be handed a DECAYED version of
the catalyst instead of the signal self-zeroing the very next tick — Task 9
only creates the store, the reset discipline, and the recording side; it does
NOT read from the store yet.

This is deliberately the simplest possible per-run store: unlike its sibling
``NewsHistoryStore`` (staleness pre-filter, embeddings + async cosine
similarity), there is no embedding step and no async I/O here — recording a
fire is a synchronous dict write.

Lifecycle — PIT correctness (mirrors ``history.py``'s spec D2 discipline):
    The store is strictly PER-RUN state.  Live trading accumulates it within
    a single process run; the backtest driver calls
    ``reset_news_last_fire_store()`` at the start of every window replay so a
    window never carries a prior window's (or a prior run's) catalyst into
    its decay logic.  It is NEVER persisted to disk.

PIT/as_of discipline:
    ``LastFire.fired_at`` is stored as an ISO-8601 STRING, never a
    ``datetime``.  Nothing datetime-shaped may leak into ADK session state
    via any downstream ``state_delta`` dump — callers must ISO-stringify
    (``.isoformat()``) before calling ``record()``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class LastFire:
    """One recorded directional news fire for a single ticker.

    Attributes:
        lean:       the fired direction — "bullish" or "bearish" (never
                    "neutral"; neutral verdicts are never recorded).
        magnitude:  the verdict's magnitude at the moment it fired.
        confidence: the verdict's confidence at the moment it fired.
        fired_at:   ISO-8601 string timestamp of the firing tick's ``as_of``
                    (never a ``datetime`` — see module PIT discipline note).
    """

    lean: str
    magnitude: float
    confidence: float
    fired_at: str


class NewsLastFireStore:
    """In-memory, per-run store of each ticker's most recent directional fire.

    Recording is synchronous and unconditional overwrite: a new fire for a
    ticker always replaces whatever was previously recorded for it, so
    ``get()`` only ever returns the single most recent catalyst.
    """

    def __init__(self) -> None:
        """Initialise an empty store."""
        # ticker → most recent directional fire.
        self._fires: dict[str, LastFire] = {}

    def record(
        self,
        ticker: str,
        *,
        lean: str,
        magnitude: float,
        confidence: float,
        fired_at: str,
    ) -> None:
        """Record (or overwrite) the most recent directional fire for ``ticker``.

        Parameters:
            ticker:     watchlist ticker symbol.
            lean:       "bullish" or "bearish" — callers must not record
                        neutral verdicts (Task 9 wiring only calls this when
                        the joiner's verdict lean is directional).
            magnitude:  the verdict's magnitude at the moment it fired.
            confidence: the verdict's confidence at the moment it fired.
            fired_at:   ISO-8601 string timestamp (as_of discipline — never
                        pass a raw ``datetime``).

        Returns:
            None.
        """
        self._fires[ticker] = LastFire(
            lean=lean,
            magnitude=magnitude,
            confidence=confidence,
            fired_at=fired_at,
        )

    def get(self, ticker: str) -> LastFire | None:
        """Return the most recent directional fire recorded for ``ticker``.

        Parameters:
            ticker: watchlist ticker symbol.

        Returns:
            The ``LastFire`` record, or ``None`` if the ticker has never
            fired a directional verdict in this run.
        """
        return self._fires.get(ticker)


# ── Module-level per-run singleton ────────────────────────────────────────
#
# Mirrors ``history.py``'s singleton pattern: one store shared by the news
# joiner for the lifetime of a process run.  The backtest driver resets it
# at the start of every window replay (see ``reset_news_last_fire_store``).

_STORE: NewsLastFireStore | None = None


def get_news_last_fire_store() -> NewsLastFireStore:
    """Return the process-wide per-run store, creating it on first use.

    Returns:
        The shared ``NewsLastFireStore`` instance for the current run.
    """
    global _STORE
    if _STORE is None:
        _STORE = NewsLastFireStore()
    return _STORE


def reset_news_last_fire_store() -> None:
    """Discard the current store so the next access builds a fresh one.

    Called by the backtest driver before each window replay — a window's
    decay logic (Task 10) must never carry a prior window's (or a prior
    run's) catalyst forward (PIT correctness, mirroring spec D2).

    Returns:
        None.
    """
    global _STORE
    _STORE = None
