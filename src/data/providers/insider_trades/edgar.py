"""`fetch` — Form 4 via `edgartools` (free EDGAR, 10 req/sec cap).

edgartools wraps SEC EDGAR directly: no API key, no quota — just a
mandatory contact email in the User-Agent. Set `EDGAR_IDENTITY` in
`.env` (e.g. ``"Oscar Hill oscar@example.com"``) and edgartools will
attach it to every request.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

from edgar import Company, set_identity

from data.registry import _LIMITERS, register
from data.retry import with_retry
from data.secrets import require_key

from ...models import InsiderTrade, TradeSide


def _ensure_identity() -> None:
    identity = require_key("EDGAR_IDENTITY")
    set_identity(identity)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _row_get(row: Any, *keys: str) -> Any:
    """Pull a named field from a DataFrame row, dict, or attribute object."""
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


def _extract(
    form4: Any, table: Any, side: TradeSide, symbol: str, filing: Any
) -> list[InsiderTrade]:
    out: list[InsiderTrade] = []
    insider = getattr(form4, "insider_name", None) or "unknown"
    title = getattr(form4, "position", None)
    filed_date = _coerce_date(getattr(filing, "filing_date", None)) or date.today()
    filed_at = datetime.combine(filed_date, datetime.min.time(), tzinfo=UTC)
    form_type = str(getattr(filing, "form", "4"))

    for row in _iter_rows(table):
        shares = _to_float(_row_get(row, "Shares", "shares", "Quantity", "quantity"))
        if shares is None or shares == 0:
            continue
        price = _to_float(
            _row_get(row, "Price", "price", "PricePerShare", "price_per_share")
        )
        txn_date = (
            _coerce_date(_row_get(row, "Date", "date", "TransactionDate", "transaction_date"))
            or filed_date
        )
        out.append(
            InsiderTrade(
                ticker=symbol,
                insider_name=str(insider),
                insider_title=str(title) if title else None,
                side=side,
                shares=shares,
                price_per_share=price,
                transaction_date=txn_date,
                filed_at=filed_at,
                form_type=form_type,
            )
        )
    return out


@with_retry
def _list_form4_filings(symbol: str, lookback_days: int) -> list[Any]:
    _ensure_identity()
    from_iso = (date.today() - timedelta(days=lookback_days)).isoformat()
    company = Company(symbol)
    filings = company.get_filings(form="4", filing_date=f"{from_iso}:")
    return list(filings.head(50))


@with_retry
def _parse_form4(filing: Any, symbol: str) -> list[InsiderTrade]:
    _ensure_identity()
    try:
        form4 = filing.obj()
    except Exception:
        return []
    purchases = getattr(form4, "common_stock_purchases", None)
    sales = getattr(form4, "common_stock_sales", None)
    out: list[InsiderTrade] = []
    out.extend(_extract(form4, purchases, "buy", symbol, filing))
    out.extend(_extract(form4, sales, "sell", symbol, filing))
    return out


@register(domain="insider_trades", name="edgar", upstream="edgar", rate_per_minute=600, burst=20)
async def fetch(ticker: str, lookback_days: int = 30) -> list[InsiderTrade]:
    """Form 4 buys/sells filed in the last `lookback_days` for `ticker`.

    Acquires one EDGAR token per filing to parse. At 10 req/sec this is
    comfortably under the SEC cap.
    """
    symbol = ticker.upper()

    filings = await asyncio.to_thread(_list_form4_filings, symbol, lookback_days)

    all_trades: list[InsiderTrade] = []
    for filing in filings:
        await _LIMITERS["edgar"].acquire()
        try:
            trades = await asyncio.to_thread(_parse_form4, filing, symbol)
        except Exception:
            continue
        all_trades.extend(trades)
    return all_trades
