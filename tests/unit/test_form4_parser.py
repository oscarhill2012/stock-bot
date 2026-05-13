"""Tests for the extended _parse_form4 — footnote + code + 10b5-1 + derivatives."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd


def _fake_form4_obj():
    """Construct a minimal SimpleNamespace mirroring the edgartools shape.

    Uses the dict/list shape (``footnote_ids: list[str]``, ``transaction_code``
    key) — representative of test-fixture / future-API rows.  The real
    edgartools API returns pandas DataFrames; that shape is covered by
    the ``_fake_form4_df`` fixture below.
    """
    return SimpleNamespace(
        common_stock_purchases=[
            {
                "shares": 1000.0,
                "price_per_share": 175.5,
                "transaction_date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "transaction_code": "P",
                "footnote_ids": ["F1"],
            },
        ],
        common_stock_sales=[
            {
                "shares": 500.0,
                "price_per_share": 180.0,
                "transaction_date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "transaction_code": "S",
                "footnote_ids": ["F2"],
            },
        ],
        derivative_securities=[
            {
                "underlying_shares": 200.0,
                "strike_price": 100.0,
                "derivative_type": "option",
                "transaction_date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "transaction_code": "M",
                "side": "buy",
                "footnote_ids": [],
            },
        ],
        footnotes={
            "F1": "Open-market purchase; not pursuant to any plan.",
            "F2": "Sale effected pursuant to a Rule 10b5-1 trading plan.",
        },
        equity_swap_or_planned_sale=False,
        filed_at="2026-05-02T13:30:00Z",
        ticker="AAPL",
        form_type="4",
    )


def _fake_form4_df():
    """Construct a SimpleNamespace whose transaction tables are pandas DataFrames.

    Row shapes match the real edgartools output:
    - ``footnotes`` column: newline-separated footnote ID strings.
    - ``Code`` column: transaction code (not ``transaction_code``).
    - ``EquitySwap`` column: per-row boolean flag (matches edgartools column name).

    This mirrors what ``Form4.common_stock_purchases`` / ``common_stock_sales``
    actually return in production.
    """
    purchases_df = pd.DataFrame(
        [
            {
                "Shares": 1000.0,
                "Price": 175.5,
                "Date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "Code": "P",
                "footnotes": "F1",       # single ID, no newline needed
                "EquitySwap": False,
            }
        ]
    )
    sales_df = pd.DataFrame(
        [
            {
                "Shares": 500.0,
                "Price": 180.0,
                "Date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "Code": "S",
                "footnotes": "F2",       # F2 text mentions 10b5-1
                "EquitySwap": False,
            }
        ]
    )
    deriv_df = pd.DataFrame(
        [
            {
                "underlying_shares": 200.0,
                "strike_price": 100.0,
                "derivative_type": "option",
                "Date": "2026-05-01",
                "insider_name": "Tim Cook",
                "insider_title": "CEO",
                "Code": "M",
                "footnotes": "",
                "EquitySwap": False,
                "side": "buy",
            }
        ]
    )
    return SimpleNamespace(
        common_stock_purchases=purchases_df,
        common_stock_sales=sales_df,
        derivative_securities=deriv_df,
        footnotes={
            "F1": "Open-market purchase; not pursuant to any plan.",
            "F2": "Sale effected pursuant to a Rule 10b5-1 trading plan.",
        },
        equity_swap_or_planned_sale=False,
        filed_at="2026-05-02T13:30:00Z",
        ticker="AAPL",
        form_type="4",
    )


# ---------------------------------------------------------------------------
# Dict-shape tests (fixture / future-API path)
# ---------------------------------------------------------------------------

def test_parse_form4_extracts_footnote_and_code():
    """A common-stock purchase row picks up its footnote text and transaction code."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_obj())
    purchases = [t for t in bundle.trades if t.side == "buy"]
    assert len(purchases) == 1
    assert purchases[0].transaction_code == "P"
    assert "Open-market" in (purchases[0].footnote or "")
    assert purchases[0].is_10b5_1 is False


def test_parse_form4_detects_10b5_1_via_footnote_regex():
    """A sale row carrying 10b5-1 footnote sets `is_10b5_1` even if form flag is False."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_obj())
    sales = [t for t in bundle.trades if t.side == "sell"]
    assert len(sales) == 1
    assert sales[0].is_10b5_1 is True


def test_parse_form4_extracts_derivative_row():
    """Derivative table produces an InsiderDerivativeTrade with strike + type."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_obj())
    assert len(bundle.derivatives) == 1
    d = bundle.derivatives[0]
    assert d.derivative_type == "option"
    assert d.strike_price == 100.0
    assert d.transaction_code == "M"


# ---------------------------------------------------------------------------
# DataFrame-shape tests (real edgartools output path)
# ---------------------------------------------------------------------------

def test_parse_form4_df_extracts_footnote_and_code():
    """DataFrame rows (real edgartools shape) resolve footnotes and read ``Code``."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_df())
    purchases = [t for t in bundle.trades if t.side == "buy"]
    assert len(purchases) == 1
    assert purchases[0].transaction_code == "P"
    assert "Open-market" in (purchases[0].footnote or "")
    assert purchases[0].is_10b5_1 is False


def test_parse_form4_df_detects_10b5_1_via_footnote_regex():
    """DataFrame sale row with 10b5-1 footnote text sets is_10b5_1=True."""
    from data.providers.insider_trades.edgar import _parse_form4

    bundle = _parse_form4(_fake_form4_df())
    sales = [t for t in bundle.trades if t.side == "sell"]
    assert len(sales) == 1
    assert sales[0].is_10b5_1 is True


def test_parse_form4_df_multi_footnote_newline():
    """A ``footnotes`` string with multiple newline-separated IDs resolves both texts."""
    from data.providers.insider_trades.edgar import _extract_footnote

    row = pd.Series({"footnotes": "F1\nF2"})
    form4 = SimpleNamespace(
        footnotes={
            "F1": "First note.",
            "F2": "Second note.",
        }
    )
    result = _extract_footnote(row, form4)
    assert result == "First note. | Second note."


# ---------------------------------------------------------------------------
# _is_planned_sale — row-level EquitySwap priority tests
# ---------------------------------------------------------------------------

def test_is_planned_sale_row_level_equity_swap_takes_precedence():
    """Row-level EquitySwap=True triggers 10b5-1 even when footnote has no regex match."""
    from data.providers.insider_trades.edgar import _is_planned_sale

    row = pd.Series({"EquitySwap": True})
    form4 = SimpleNamespace(equity_swap_or_planned_sale=False)
    # Footnote contains no 10b5-1 keyword — row flag alone should suffice.
    assert _is_planned_sale(row, form4, footnote="Regular open-market sale.") is True


def test_is_planned_sale_row_level_false_does_not_fall_through_to_form():
    """Row-level EquitySwap=False blocks the form-level flag — row is authoritative.

    When the row-level column is *present* (even as False), the form-level
    ``equity_swap_or_planned_sale`` flag must NOT fire.  Only the footnote
    regex can still upgrade the result.
    """
    from data.providers.insider_trades.edgar import _is_planned_sale

    row = pd.Series({"EquitySwap": False})
    form4 = SimpleNamespace(equity_swap_or_planned_sale=True)
    # No footnote match — row-level False wins, form-level True is blocked.
    assert _is_planned_sale(row, form4, footnote=None) is False


def test_is_planned_sale_row_absent_uses_form_level():
    """When row has no EquitySwap column, form-level flag is the fallback."""
    from data.providers.insider_trades.edgar import _is_planned_sale

    # Row with no EquitySwap attribute at all — _row_get returns None.
    row = pd.Series({"Shares": 100.0})
    form4 = SimpleNamespace(equity_swap_or_planned_sale=True)
    assert _is_planned_sale(row, form4, footnote=None) is True


def test_is_planned_sale_form_level_does_not_bleed_to_row_with_equity_swap_false():
    """When a row has an explicit EquitySwap=False, the form-level flag does NOT bleed through.

    Row-level decision is authoritative.  A row that explicitly carries
    EquitySwap=False on a mixed filing must NOT be tagged 10b5-1 just because
    another row on the same filing has ``equity_swap_or_planned_sale=True``.
    """
    from data.providers.insider_trades.edgar import _is_planned_sale

    # Row explicitly says False — must not be overridden by form-level True.
    row = pd.Series({"EquitySwap": False})
    form4 = SimpleNamespace(equity_swap_or_planned_sale=True)

    # Footnote has no 10b5-1 keyword — form-level flag must be blocked.
    result = _is_planned_sale(row, form4, footnote="Open-market buy.")

    assert result is False


def test_is_planned_sale_row_false_but_footnote_regex_applies():
    """Row-level EquitySwap=False + 10b5-1 footnote → is_10b5_1=True.

    Even when the row-level flag is explicitly False, the footnote regex
    still fires.  This covers the edge case where the EquitySwap column is
    present but the 10b5-1 plan is documented only in the footnote text.
    """
    from data.providers.insider_trades.edgar import _is_planned_sale

    row = pd.Series({"EquitySwap": False})
    form4 = SimpleNamespace(equity_swap_or_planned_sale=False)
    # Footnote explicitly mentions a 10b5-1 plan — regex should catch it.
    result = _is_planned_sale(
        row, form4, footnote="Sale pursuant to a 10b5-1 trading plan."
    )
    assert result is True


def test_is_planned_sale_all_false_returns_false():
    """No flags set and no regex match → is_10b5_1=False."""
    from data.providers.insider_trades.edgar import _is_planned_sale

    row = pd.Series({"EquitySwap": False})
    form4 = SimpleNamespace(equity_swap_or_planned_sale=False)
    assert _is_planned_sale(row, form4, footnote="Open-market buy.") is False
