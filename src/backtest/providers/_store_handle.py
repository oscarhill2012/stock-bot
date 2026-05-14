"""Module-level singleton wiring for the cache providers.

The runner instantiates ``CachedDataStore`` once and calls ``set_store(store)``;
every cache provider reads from this singleton.  Keeping the wiring in one
module keeps the providers themselves stateless and trivially testable.

Usage
-----
At backtest run start::

    from backtest.cache.store import CachedDataStore
    from backtest.providers._store_handle import set_store

    store = CachedDataStore(path)
    set_store(store)

In tests, call ``clear_store()`` in teardown to prevent state leaking between
test cases.
"""
from __future__ import annotations

from backtest.cache.store import CachedDataStore

# Module-level singleton; None until the runner calls set_store().
_store: CachedDataStore | None = None


def set_store(store: CachedDataStore) -> None:
    """Install the cache store the providers should read from this run.

    Parameters
    ----------
    store:
        A fully initialised ``CachedDataStore`` instance.
    """
    global _store
    _store = store


def get_store() -> CachedDataStore:
    """Return the configured store; raise if the runner has not called ``set_store``.

    Returns
    -------
    CachedDataStore
        The active store singleton.

    Raises
    ------
    RuntimeError
        If ``set_store`` has not been called yet (programming error — not a
        cache miss).
    """
    if _store is None:
        raise RuntimeError(
            "cache providers used before runner called set_store(); "
            "this should never happen in a real backtest run"
        )
    return _store


def clear_store() -> None:
    """Reset the singleton — used between tests to prevent state leakage."""
    global _store
    _store = None
