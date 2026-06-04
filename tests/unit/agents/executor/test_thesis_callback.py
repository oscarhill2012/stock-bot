"""Unit tests for the ``_build_fill_prices`` helper (A-068).

These tests verify that the fill-price lookup built from ``state["executions"]``
reads only the canonical ``actual_price`` key and omits rejected / null rows
entirely (no ``None`` entries in the returned dict).

See audit A-068: the old implementation accepted both ``fill_price`` and
``actual_price`` spellings, fell back to ``None`` for rejected rows, and used
a dead ``stance`` key as a ticker fallback.  All three defects are covered here.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_fill_prices_uses_only_actual_price_key():
    """A-068 — fill_prices must read the canonical actual_price field, not
    the deprecated fill_price alias. A row written with the alias must NOT
    contribute a price (alias support is removed).
    """

    from agents.executor.agent import _build_fill_prices  # noqa: PLC0415

    state = {
        "executions": [
            {"order": {"ticker": "AAPL"}, "actual_price": 195.0},
            {"order": {"ticker": "MSFT"}, "fill_price":   400.0},   # alias — must be ignored
        ],
    }

    fill_prices = _build_fill_prices(state)

    assert fill_prices == {"AAPL": 195.0}, (
        "Only the canonical actual_price key must populate fill_prices; "
        "the fill_price alias was dual-spelling support that has been removed."
    )


def test_fill_prices_omits_none_and_missing_actual_price():
    """Rows where actual_price is None or absent must be omitted entirely.

    The old code stored ``None`` for such rows, silently feeding the
    BUY-without-price path that Task 5 (A-008) made loud.  Ensuring the
    dict never contains ``None`` values is the correct fix.
    """

    from agents.executor.agent import _build_fill_prices  # noqa: PLC0415

    state = {
        "executions": [
            # Canonical row — must appear.
            {"order": {"ticker": "AAPL"}, "actual_price": 195.0},
            # Rejected fill (actual_price is None) — must be omitted.
            {"order": {"ticker": "TSLA"}, "actual_price": None, "status": "rejected"},
            # No actual_price key at all — must be omitted.
            {"order": {"ticker": "GOOG"}, "status": "rejected"},
        ],
    }

    fill_prices = _build_fill_prices(state)

    assert fill_prices == {"AAPL": 195.0}, (
        "Rows with None or missing actual_price must be omitted from fill_prices; "
        "None entries silently become buy-without-price failures."
    )
    # Confirm no None values leaked in.
    assert all(v is not None for v in fill_prices.values()), (
        "fill_prices must never contain None values."
    )


def test_fill_prices_dead_stance_fallback_not_used():
    """The old code fell back to row['stance']['ticker'] when row['order'] was absent.

    Execution rows always carry 'order', never 'stance'.  A row without 'order'
    must yield no entry — the dead fallback must not conjure a ticker.
    """

    from agents.executor.agent import _build_fill_prices  # noqa: PLC0415

    state = {
        "executions": [
            # Malformed row: has 'stance' but no 'order' — old code would have
            # used the stance fallback; new code must ignore this row entirely.
            {"stance": {"ticker": "NVDA"}, "actual_price": 900.0},
        ],
    }

    fill_prices = _build_fill_prices(state)

    assert fill_prices == {}, (
        "A row with no 'order' key must produce no entry; "
        "the dead stance-ticker fallback must not be used."
    )
