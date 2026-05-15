"""Module-level singleton wiring for the cache providers.

The runner instantiates ``CachedDataStore`` once and calls ``set_store(store)``;
every cache provider reads from this singleton.  Keeping the wiring in one
module keeps the providers themselves stateless and trivially testable.
"""
from __future__ import annotations

from backtest.cache.store import CachedDataStore

_store: CachedDataStore | None = None


def set_store(store: CachedDataStore) -> None:
    """Install the cache store the providers should read from this run.

    Parameters
    ----------
    store:
        Fully-initialised ``CachedDataStore`` instance.  The runner calls
        this once before dispatching any cache-domain fetch.
    """
    global _store
    _store = store


def get_store() -> CachedDataStore:
    """Return the configured store; raise if the runner has not called ``set_store``.

    Returns
    -------
    CachedDataStore
        The singleton store installed by the runner.

    Raises
    ------
    RuntimeError
        If ``set_store`` has not been called yet (i.e. the runner forgot to
        wire the store before dispatching).
    """
    if _store is None:
        raise RuntimeError(
            "cache providers used before runner called set_store(); "
            "this should never happen in a real backtest run"
        )
    return _store


def clear_store() -> None:
    """Reset the singleton — used between tests to prevent state leaking across cases."""
    global _store
    _store = None
