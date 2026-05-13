"""`fetch` — Form 4 via `edgartools` (free EDGAR, 10 req/sec cap).

edgartools wraps SEC EDGAR directly: no API key, no quota — just a
mandatory contact email in the User-Agent. Set `EDGAR_IDENTITY` in
`.env` (e.g. ``"Oscar Hill oscar@example.com"``) and edgartools will
attach it to every request.
"""
from __future__ import annotations

import asyncio
import contextlib
import math
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from edgar import Company, set_identity

from data.registry import _LIMITERS, register
from data.retry import with_retry
from data.secrets import require_key

from ...models import Form4Bundle, InsiderDerivativeTrade, InsiderTrade, TradeSide

# ---------------------------------------------------------------------------
# 10b5-1 detection regex — matches "10b5-1", "10b5 1", "10b51" (case-insensitive).
# Used as a fallback when the form-level flag is absent or False.
# ---------------------------------------------------------------------------
_TEN_B5_1_RE = re.compile(r"10b5[-\s]?1", re.IGNORECASE)


def _ensure_identity() -> None:
    """Load the EDGAR identity string from the environment and register it."""
    identity = require_key("EDGAR_IDENTITY")
    set_identity(identity)


def _to_float(v: Any) -> float | None:
    """Coerce `v` to a finite float, returning None if not possible."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _row_get(row: Any, *keys: str) -> Any:
    """Pull a named field from a DataFrame row, dict, or attribute object.

    Tries each key in order; returns the first non-None value found.
    Supports dict `.get()`, subscript access, and `getattr`.
    """
    for k in keys:
        if hasattr(row, "get"):
            try:
                v = row.get(k)
                if v is not None:
                    return v
            except Exception:
                pass
        try:
            v = row[k]
            if v is not None:
                return v
        except (KeyError, TypeError, IndexError):
            pass
        v = getattr(row, k, None)
        if v is not None:
            return v
    return None


def _iter_rows(table: Any) -> Iterator[Any]:
    """Yield each row from a table that may be a DataFrame, list, or None."""
    if table is None:
        return
    try:
        if len(table) == 0:
            return
    except TypeError:
        return
    if hasattr(table, "iterrows"):
        for _, row in table.iterrows():
            yield row
    else:
        for row in table:
            yield row


def _coerce_date(v: Any) -> date | None:
    """Coerce `v` to a `date` from datetime, date, or ISO-string."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v)[:10]).date()
    except ValueError:
        return None


def _coerce_filed_at(v: Any, fallback_date: date) -> datetime:
    """Coerce `v` to a timezone-aware datetime for `filed_at`.

    Accepts datetime objects or ISO strings. Falls back to midnight UTC
    on `fallback_date` if parsing fails or `v` is None.
    """
    if v is None:
        return datetime.combine(fallback_date, datetime.min.time(), tzinfo=UTC)
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v
    # Try to parse as ISO string — strip trailing 'Z' for Python < 3.11 compat.
    try:
        s = str(v).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return datetime.combine(fallback_date, datetime.min.time(), tzinfo=UTC)


def _extract_footnote(row: Any, form4: object) -> str | None:
    """Resolve footnote IDs for a row against the form-level footnote map.

    Handles two row shapes:
    - dict / SimpleNamespace carrying ``footnote_ids: list[str]`` (test fixtures,
      future provider shapes).
    - pandas Series (real edgartools output) carrying a ``footnotes`` string of
      newline-separated IDs (e.g. ``"F1\\nF2"``).

    Joins resolved footnote texts with ' | '. Returns None when no IDs are
    present or the map holds no matching text.

    Parameters
    ----------
    row:
        The transaction row — dict, SimpleNamespace, or pandas Series.
    form4:
        The parsed Form 4 object carrying a `.footnotes` mapping of
        footnote ID → text (dict or edgartools Footnotes object).
    """
    ids: list[str] = []

    # Try the list-of-IDs shape first (dict / SimpleNamespace fixtures).
    raw_ids = _row_get(row, "footnote_ids")
    if isinstance(raw_ids, list):
        ids = [str(fid) for fid in raw_ids if fid]
    else:
        # Fall back to the newline-separated string in the real edgartools Series.
        raw_str = _row_get(row, "footnotes") or ""
        ids = [fid.strip() for fid in str(raw_str).split("\n") if fid.strip()]

    fmap = getattr(form4, "footnotes", {}) or {}
    parts: list[str] = []
    for fid in ids:
        text = None
        try:
            # ``fmap`` is either a plain dict or edgartools' Footnotes object;
            # both support subscript access, Footnotes also exposes ``.get``.
            text = fmap[fid] if hasattr(fmap, "__getitem__") else fmap.get(fid)
        except (KeyError, TypeError):
            text = None
        if text:
            parts.append(str(text))

    return " | ".join(parts) if parts else None


def _is_planned_sale(row: Any, form4: object, footnote: str | None) -> bool:
    """Determine whether a row is a planned (10b5-1) trade.

    Priority:
    - If the row-level ``EquitySwap`` column is **present**, it is authoritative
      for that row. Form-level flags do not override an explicit row-level False;
      footnote-regex fallback still applies on a row-level False.
    - If the row-level column is **absent**, the form-level
      ``equity_swap_or_planned_sale`` flag is consulted, then the footnote regex.

    Parameters
    ----------
    row:
        The transaction row — checked for a row-level EquitySwap value.
    form4:
        The parsed Form 4 object; checked for ``equity_swap_or_planned_sale``.
    footnote:
        The resolved footnote text for this row, or None.
    """
    # Row-level flag: note we only look for row-specific keys here —
    # ``equity_swap_or_planned_sale`` is a form-level attribute, not a row column,
    # so including it in _row_get would conflate the two levels.
    row_flag = _row_get(row, "EquitySwap", "equity_swap")
    if row_flag is not None:
        # Column present — honour the row-level decision; do not fall through
        # to the form-level flag (which can bleed across rows on mixed filings).
        return bool(row_flag) or bool(footnote and _TEN_B5_1_RE.search(footnote))

    # Form-level fallback — applies only when no row-level column is present.
    if bool(getattr(form4, "equity_swap_or_planned_sale", False)):
        return True

    return bool(footnote and _TEN_B5_1_RE.search(footnote))


def _parse_form4(form4: Any) -> Form4Bundle:
    """Parse a pre-resolved Form 4 object into a `Form4Bundle`.

    Reads both the common-stock transaction table (Table I) and the
    derivative-securities table (Table II), resolving footnotes, reading
    transaction codes, and detecting 10b5-1 plans.

    This function accepts a form4 object that already exposes the parsed
    attributes (as returned by ``edgartools``'s ``filing.obj()``), not
    the raw EDGAR filing. Callers are responsible for calling
    ``filing.obj()`` first.

    Parameters
    ----------
    form4:
        The parsed Form 4 object. Expected attributes:
        - ``common_stock_purchases`` — iterable of row dicts.
        - ``common_stock_sales`` — iterable of row dicts.
        - ``derivative_securities`` — iterable of row dicts.
        - ``footnotes`` — dict mapping footnote ID → text.
        - ``equity_swap_or_planned_sale`` — bool form-level flag.
        - ``ticker`` — the issuer ticker symbol.
        - ``form_type`` — e.g. "4", "4/A".
        - ``filed_at`` — the filing date/time (str or datetime).
    """
    # Resolve filing-level context shared across all rows.
    symbol: str = str(getattr(form4, "ticker", "") or "")
    # Parser fallback to "4" is intentional: edgartools may surface amended
    # forms as "4/A" but the attribute can occasionally be absent on malformed
    # XML; the model no longer carries a default so we supply one here.
    form_type: str = str(getattr(form4, "form_type", "4") or "4")
    filed_at_raw = getattr(form4, "filed_at", None)
    today = date.today()
    filed_at: datetime = _coerce_filed_at(filed_at_raw, today)
    filed_date: date = filed_at.date()

    # Insider identity may be on the form object or on each row.
    form_insider: str = str(getattr(form4, "insider_name", "") or "")
    form_title: str | None = getattr(form4, "position", None)

    trades: list[InsiderTrade] = []
    derivatives: list[InsiderDerivativeTrade] = []

    # -----------------------------------------------------------------------
    # Table I — common-stock purchases and sales.
    # -----------------------------------------------------------------------
    purchases_table = getattr(form4, "common_stock_purchases", None)
    for row in _iter_rows(purchases_table):
        _build_trade(
            row=row,
            form4=form4,
            form_insider=form_insider,
            form_title=form_title,
            side="buy",
            symbol=symbol,
            form_type=form_type,
            filed_at=filed_at,
            filed_date=filed_date,
            out=trades,
        )

    sales_table = getattr(form4, "common_stock_sales", None)
    for row in _iter_rows(sales_table):
        _build_trade(
            row=row,
            form4=form4,
            form_insider=form_insider,
            form_title=form_title,
            side="sell",
            symbol=symbol,
            form_type=form_type,
            filed_at=filed_at,
            filed_date=filed_date,
            out=trades,
        )

    # -----------------------------------------------------------------------
    # Table II — derivative-securities transactions.
    # -----------------------------------------------------------------------
    deriv_table = getattr(form4, "derivative_securities", None)
    for row in _iter_rows(deriv_table):
        _build_derivative(
            row=row,
            form4=form4,
            form_insider=form_insider,
            form_title=form_title,
            symbol=symbol,
            filed_at=filed_at,
            filed_date=filed_date,
            out=derivatives,
        )

    return Form4Bundle(trades=trades, derivatives=derivatives)


def _build_trade(
    *,
    row: Any,
    form4: Any,
    form_insider: str,
    form_title: str | None,
    side: TradeSide,
    symbol: str,
    form_type: str,
    filed_at: datetime,
    filed_date: date,
    out: list[InsiderTrade],
) -> None:
    """Build one InsiderTrade from a common-stock row and append to `out`.

    Skips rows with no parseable share count (zero or None).
    """
    shares = _to_float(_row_get(row, "Shares", "shares", "Quantity", "quantity"))
    if shares is None or shares == 0:
        return

    price = _to_float(
        _row_get(row, "Price", "price", "PricePerShare", "price_per_share")
    )
    txn_date = (
        _coerce_date(_row_get(row, "Date", "date", "TransactionDate", "transaction_date"))
        or filed_date
    )

    # Row-level insider identity overrides form-level when present.
    insider_name = str(
        _row_get(row, "insider_name", "InsiderName", "name") or form_insider or "unknown"
    )
    insider_title = str(
        _row_get(row, "insider_title", "InsiderTitle", "title") or form_title or ""
    ) or None

    # Narrative supplement: footnotes, transaction code, 10b5-1 flag.
    # Pass ``row`` directly — _extract_footnote handles both dict and Series.
    footnote = _extract_footnote(row, form4)
    tx_code = str(
        _row_get(row, "transaction_code", "TransactionCode", "Code", "code") or ""
    ) or None
    is_planned = _is_planned_sale(row, form4, footnote)

    out.append(
        InsiderTrade(
            ticker=symbol,
            insider_name=insider_name,
            insider_title=insider_title,
            side=side,
            shares=shares,
            price_per_share=price,
            transaction_date=txn_date,
            filed_at=filed_at,
            form_type=form_type,
            transaction_code=tx_code,
            is_10b5_1=is_planned,
            footnote=footnote,
        )
    )


def _build_derivative(
    *,
    row: Any,
    form4: Any,
    form_insider: str,
    form_title: str | None,
    symbol: str,
    filed_at: datetime,
    filed_date: date,
    out: list[InsiderDerivativeTrade],
) -> None:
    """Build one InsiderDerivativeTrade from a derivative-securities row.

    Skips rows with no parseable underlying share count.
    """
    underlying_shares = _to_float(
        _row_get(row, "underlying_shares", "UnderlyingShares", "shares", "Shares")
    )
    if underlying_shares is None or underlying_shares == 0:
        return

    strike = _to_float(
        _row_get(row, "strike_price", "StrikePrice", "ExercisePrice", "exercise_price")
    )
    txn_date = (
        _coerce_date(_row_get(row, "transaction_date", "Date", "date", "TransactionDate"))
        or filed_date
    )

    insider_name = str(
        _row_get(row, "insider_name", "InsiderName", "name") or form_insider or "unknown"
    )
    insider_title = str(
        _row_get(row, "insider_title", "InsiderTitle", "title") or form_title or ""
    ) or None

    # Derivative type — "option", "rsu", "warrant", "performance_award", etc.
    deriv_type = str(
        _row_get(row, "derivative_type", "DerivativeType", "security_type") or ""
    ) or None

    # Side may be on the row or default to buy (most derivative rows are grants/exercises).
    side_raw = str(_row_get(row, "side", "Side", "transaction_side") or "buy").lower()
    side: TradeSide = side_raw if side_raw in ("buy", "sell", "exchange", "unknown") else "buy"  # type: ignore[assignment]

    # Pass ``row`` directly — _extract_footnote handles both dict and Series.
    footnote = _extract_footnote(row, form4)
    tx_code = str(
        _row_get(row, "transaction_code", "TransactionCode", "Code", "code") or ""
    ) or None
    is_planned = _is_planned_sale(row, form4, footnote)

    out.append(
        InsiderDerivativeTrade(
            ticker=symbol,
            insider_name=insider_name,
            insider_title=insider_title,
            side=side,
            derivative_type=deriv_type,
            underlying_shares=underlying_shares,
            strike_price=strike,
            transaction_date=txn_date,
            filed_at=filed_at,
            transaction_code=tx_code,
            is_10b5_1=is_planned,
            footnote=footnote,
        )
    )


@with_retry
def _list_form4_filings(symbol: str, lookback_days: int) -> list[Any]:
    """Fetch the list of Form 4 filings for `symbol` within the lookback window.

    Returns up to 50 filings ordered by recency. Runs synchronously inside
    `asyncio.to_thread` at the call site.
    """
    _ensure_identity()
    from_iso = (date.today() - timedelta(days=lookback_days)).isoformat()
    company = Company(symbol)
    filings = company.get_filings(form="4", filing_date=f"{from_iso}:")
    return list(filings.head(50))


@with_retry
def _fetch_and_parse_one(filing: Any, symbol: str) -> Form4Bundle:
    """Resolve one raw EDGAR filing object to a `Form4Bundle`.

    Calls ``filing.obj()`` to trigger the edgartools parse, then passes
    the resulting form4 object to ``_parse_form4``.

    Returns an empty bundle if the filing cannot be parsed (e.g. bad XML).

    Parameters
    ----------
    filing:
        A raw edgartools filing entry (returned by ``filings.head(...)``).
    symbol:
        The issuer ticker, used as a fallback when the form4 object does
        not carry its own `.ticker` attribute.
    """
    _ensure_identity()
    try:
        form4 = filing.obj()
    except Exception:
        return Form4Bundle()

    # Inject the symbol as a fallback if the edgartools form4 object does
    # not expose `.ticker` directly.
    if not getattr(form4, "ticker", None):
        with contextlib.suppress(AttributeError, TypeError):
            form4.ticker = symbol  # type: ignore[attr-defined]

    return _parse_form4(form4)


@register(domain="insider_trades", name="edgar", upstream="edgar", rate_per_minute=600, burst=20)
async def fetch(ticker: str, lookback_days: int = 30) -> Form4Bundle:
    """Form 4 buys/sells and derivatives filed in the last `lookback_days` for `ticker`.

    Acquires one EDGAR token per filing to parse. At 10 req/sec this is
    comfortably under the SEC cap.

    Returns a `Form4Bundle` combining all trade rows and all derivative
    rows across every filing found in the lookback window.
    """
    symbol = ticker.upper()

    filings = await asyncio.to_thread(_list_form4_filings, symbol, lookback_days)

    all_trades: list[InsiderTrade] = []
    all_derivatives: list[InsiderDerivativeTrade] = []

    for filing in filings:
        await _LIMITERS["edgar"].acquire()
        try:
            bundle = await asyncio.to_thread(_fetch_and_parse_one, filing, symbol)
        except Exception:
            continue
        all_trades.extend(bundle.trades)
        all_derivatives.extend(bundle.derivatives)

    return Form4Bundle(trades=all_trades, derivatives=all_derivatives)
