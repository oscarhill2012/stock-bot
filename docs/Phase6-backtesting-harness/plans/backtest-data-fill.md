# Backtest Data Fill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every live data provider PIT-correct (`as_of`-aware) so the existing backtest cache-fill script (`scripts/backtest_fetch.py`) can populate the golden cache for historical windows; swap three providers for free PIT-correct alternatives (Tiingo news, FMP politician trades, edgartools+yfinance composite ratios); keep the live data surface identical so analyst agents see the same shape in live and backtest.

**Architecture:** Live wrappers in `src/data/__init__.py` already pass `as_of` to `_dispatch()`; this plan brings every leaf provider into compliance with the shape used by `src/backtest/providers/` (`*, as_of: datetime, ..., **_unused`). New providers are registered alongside fallbacks so a single `config/data.json` edit flips between sources. The backfill script is then refactored to call the public wrappers instead of an inline factory.

**Tech Stack:** Python 3.12, asyncio, edgartools, yfinance, httpx, requests, pytest (with `asyncio` + `slow` markers), SQLAlchemy/SQLite.

**Reference spec:** `docs/superpowers/specs/backtest-data-fill-design.md`.

**Shell convention:** Bash tool runs in the project root. Never prepend `cd <root> &&`. Run pytest as `PYTHONPATH=src .venv/bin/python -m pytest …`, ruff as `PYTHONPATH=src .venv/bin/python -m ruff check …`.

---

## Task 1: Plumb `as_of` through `insider_trades/edgar.py`

**Files:**
- Modify: `src/data/providers/insider_trades/edgar.py` (lines 440-512)
- Test: `tests/unit/data/providers/test_insider_trades_edgar_as_of.py` (new)

**What & why:** Today `_list_form4_filings` uses `date.today()` as the upper bound; the bot would consume future data if run at a backtest `as_of`. Add `as_of: datetime` (required kwarg) to `fetch` and `_list_form4_filings`; anchor the lookback on `as_of.date()` instead of `date.today()`. The `fetch` signature must also accept `**_unused` so other registered providers' kwargs don't cause a `TypeError` when the registry dispatches.

- [ ] **Step 1: Create the test directory tree**

```bash
mkdir -p tests/unit/data/providers
touch tests/unit/data/providers/__init__.py
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/data/providers/test_insider_trades_edgar_as_of.py`:

```python
"""``insider_trades/edgar.fetch`` honours ``as_of`` for the date window."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_lookback(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` must derive the filing-date window from ``as_of``, not wall-clock today."""
    import data.providers.insider_trades.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, lookback_days: int, as_of: datetime) -> list:
        captured["symbol"]        = symbol
        captured["lookback_days"] = lookback_days
        captured["as_of"]         = as_of
        return []

    monkeypatch.setattr(mod, "_list_form4_filings", fake_list)

    await mod.fetch(
        "AAPL",
        lookback_days=30,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["symbol"]        == "AAPL"
    assert captured["lookback_days"] == 30
    assert captured["as_of"]         == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_fetch_swallows_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider must accept extra kwargs other providers care about (``**_unused``)."""
    import data.providers.insider_trades.edgar as mod

    monkeypatch.setattr(mod, "_list_form4_filings", lambda s, l, a: [])

    # ``from_date`` is meaningless to insider_trades but news providers take it —
    # the registry dispatches the same kwargs to every domain.
    result = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )

    assert result.trades == []
```

- [ ] **Step 3: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_insider_trades_edgar_as_of.py -v`

Expected: FAIL — `fetch() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 4: Update `_list_form4_filings`**

In `src/data/providers/insider_trades/edgar.py`, replace lines 440-451:

```python
@with_retry
def _list_form4_filings(symbol: str, lookback_days: int, as_of: datetime) -> list[Any]:
    """Fetch the list of Form 4 filings for ``symbol`` within the lookback window.

    The window is anchored on ``as_of`` so backfill calls see only filings that
    existed at that historical moment.  Live callers pass ``datetime.now(UTC)``
    via the public wrapper, so behaviour is unchanged in production.

    Returns up to 50 filings ordered by recency.
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    lower_iso = (as_of.date() - timedelta(days=lookback_days)).isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form="4", filing_date=f"{lower_iso}:{upper_iso}")
    return list(filings.head(50))
```

- [ ] **Step 5: Update `fetch`**

Replace lines 486-512:

```python
@register(domain="insider_trades", name="edgar", upstream="edgar", rate_per_minute=600, burst=20)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 30,
    **_unused,
) -> Form4Bundle:
    """Form 4 buys/sells and derivatives filed in ``(as_of - lookback_days, as_of]`` for ``ticker``.

    Parameters
    ----------
    ticker:
        Symbol (uppercased internally).
    as_of:
        Upper bound for the filing window.  Live callers receive
        ``datetime.now(UTC)`` from the public wrapper; backfill callers pass the
        historical window-end timestamp.
    lookback_days:
        How many calendar days back from ``as_of`` to look.
    _unused:
        Absorbs kwargs other providers (e.g. news ``from_date``/``to_date``) use.

    Acquires one EDGAR token per filing to parse.  At 10 req/sec this is
    comfortably under the SEC cap.
    """
    symbol = ticker.upper()

    filings = await asyncio.to_thread(
        _list_form4_filings, symbol, lookback_days, as_of,
    )

    all_trades:      list[InsiderTrade]           = []
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
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_insider_trades_edgar_as_of.py -v`

Expected: 2 passed.

- [ ] **Step 7: Run the existing provider-registration test to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_provider_registration.py -v -k insider_trades`

Expected: 1 passed (`test_insider_trades_edgar_registers_on_import`).

- [ ] **Step 8: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/insider_trades/ tests/unit/data/providers/`

Expected: `All checks passed!`

- [ ] **Step 9: Commit**

```bash
git add src/data/providers/insider_trades/edgar.py \
        tests/unit/data/providers/__init__.py \
        tests/unit/data/providers/test_insider_trades_edgar_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): insider_trades/edgar honours as_of for filing window

Adds as_of (required kwarg) + **_unused to fetch and threads it through
_list_form4_filings.  Previously the upper bound was date.today(), so
backtest calls would have leaked future-filed Form 4 data into a
historical as_of.  Live behaviour unchanged — wrapper passes
datetime.now(UTC).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Plumb `as_of` through `notable_holders/edgar.py`

**Files:**
- Modify: `src/data/providers/notable_holders/edgar.py` (lines 95-133)
- Test: `tests/unit/data/providers/test_notable_holders_edgar_as_of.py` (new)

**What & why:** Same bug class as Task 1 — `_list_holder_filings` uses `date.today()`. Move it onto `as_of.date()` and add the standard signature.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_notable_holders_edgar_as_of.py`:

```python
"""``notable_holders/edgar.fetch`` honours ``as_of`` for the filing window."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_filing_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` derives the filing-date window from ``as_of``."""
    import data.providers.notable_holders.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, lookback_days: int, limit: int, as_of: datetime) -> list:
        captured["symbol"]        = symbol
        captured["lookback_days"] = lookback_days
        captured["as_of"]         = as_of
        return []

    monkeypatch.setattr(mod, "_list_holder_filings", fake_list)

    await mod.fetch(
        "AAPL",
        lookback_days=180,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["as_of"] == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)
    assert captured["lookback_days"] == 180


@pytest.mark.asyncio
async def test_fetch_accepts_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``**_unused`` lets the registry dispatch any kwarg safely."""
    import data.providers.notable_holders.edgar as mod

    monkeypatch.setattr(mod, "_list_holder_filings", lambda s, l, lim, a: [])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )
    assert out == []
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_notable_holders_edgar_as_of.py -v`

Expected: FAIL — `fetch() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Update `_list_holder_filings` and `fetch`**

In `src/data/providers/notable_holders/edgar.py`:

Replace lines 95-101:

```python
@with_retry
def _list_holder_filings(
    symbol: str,
    lookback_days: int,
    limit: int,
    as_of: datetime,
) -> list[Any]:
    """List SC 13D/13G/13F filings naming ``symbol`` in ``(as_of - lookback, as_of]``.

    Anchored on ``as_of`` so backfill sees only filings that existed historically.
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    lower_iso = (as_of.date() - timedelta(days=lookback_days)).isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form=list(_FORMS), filing_date=f"{lower_iso}:{upper_iso}")
    return list(filings.head(max(1, min(limit, 50))))
```

Replace lines 104-133:

```python
@register(
    domain="notable_holders",
    name="edgar",
    upstream="edgar",
    rate_per_minute=600,
    burst=20,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 180,
    limit: int = 20,
    **_unused,
) -> list[NotableHolder]:
    """Recent SC 13D/13G (and amendment) filings naming ``ticker`` as subject.

    ``lookback_days`` defaults to 180 since these filings are infrequent
    relative to Form 4.  ``limit`` caps how many we return after sorting.
    """
    symbol = ticker.upper()

    filings = await asyncio.to_thread(
        _list_holder_filings, symbol, lookback_days, limit, as_of,
    )

    out: list[NotableHolder] = []
    for filing in filings:
        try:
            built = _build(filing, symbol)
        except Exception:
            continue
        if built is not None:
            out.append(built)
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_notable_holders_edgar_as_of.py -v`

Expected: 2 passed.

- [ ] **Step 5: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/notable_holders/ tests/unit/data/providers/test_notable_holders_edgar_as_of.py`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/data/providers/notable_holders/edgar.py \
        tests/unit/data/providers/test_notable_holders_edgar_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): notable_holders/edgar honours as_of for filing window

Same fix as insider_trades — derive filing-date window from as_of
instead of date.today() so backfill sees historically-correct
SC 13D/13G filings.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Plumb `as_of` through `filings/edgar.py`

**Files:**
- Modify: `src/data/providers/filings/edgar.py` (lines 108-154)
- Test: `tests/unit/data/providers/test_filings_edgar_as_of.py` (new)

**What & why:** `_list_filings` has no date filter at all today — it returns the latest 50 regardless of when they were filed. Add `filing_date=":{as_of_iso}"` so backfill cannot see post-`as_of` filings.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_filings_edgar_as_of.py`:

```python
"""``filings/edgar.fetch`` filters by ``as_of`` so backfill ignores future filings."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_passes_as_of_to_lister(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_list_filings`` must receive ``as_of`` and apply it as the upper bound."""
    import data.providers.filings.edgar as mod

    captured: dict = {}

    def fake_list(symbol: str, form_types: tuple, limit: int, as_of: datetime) -> list:
        captured["symbol"]     = symbol
        captured["form_types"] = form_types
        captured["limit"]      = limit
        captured["as_of"]      = as_of
        return []

    monkeypatch.setattr(mod, "_list_filings", fake_list)

    await mod.fetch(
        "AAPL",
        form_types=("10-K", "10-Q"),
        limit=5,
        include_excerpts=False,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["as_of"]      == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)
    assert captured["form_types"] == ("10-K", "10-Q")
    assert captured["limit"]      == 5


@pytest.mark.asyncio
async def test_fetch_accepts_extra_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.filings.edgar as mod
    monkeypatch.setattr(mod, "_list_filings", lambda s, ft, lim, a: [])
    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )
    assert out == []
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_filings_edgar_as_of.py -v`

Expected: FAIL — `fetch() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Update `_list_filings`**

In `src/data/providers/filings/edgar.py`, replace lines 108-113:

```python
@with_retry
def _list_filings(
    symbol: str,
    form_types: tuple[str, ...],
    limit: int,
    as_of: datetime,
) -> list[Any]:
    """List the most recent ``limit`` filings of ``form_types`` for ``symbol``, filed on or before ``as_of``.

    Uses the SEC's ``filing_date=":YYYY-MM-DD"`` upper-bound syntax so the
    backfill never sees filings that did not yet exist at ``as_of``.
    """
    _ensure_identity()
    upper_iso = as_of.date().isoformat()
    company   = Company(symbol)
    filings   = company.get_filings(form=list(form_types), filing_date=f":{upper_iso}")
    return list(filings.head(max(1, min(limit, 50))))
```

- [ ] **Step 4: Update `fetch`**

Replace lines 122-154:

```python
@register(
    domain="filings",
    name="edgar",
    upstream="edgar",
    rate_per_minute=600,
    burst=20,
)
async def fetch(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    as_of: datetime,
    include_excerpts: bool = True,
    **_unused,
) -> list[Filing]:
    """Latest ``limit`` filings of ``form_types`` for ``ticker`` filed on or before ``as_of``."""
    symbol = ticker.upper()

    filings = await asyncio.to_thread(
        _list_filings, symbol, form_types, limit, as_of,
    )

    out: list[Filing] = []
    for filing in filings:
        if include_excerpts:
            await _LIMITERS["edgar"].acquire()
        try:
            built = await asyncio.to_thread(
                _build_filing_with_identity, filing, symbol, include_excerpts
            )
        except Exception:
            continue
        out.append(built)
    return out
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_filings_edgar_as_of.py -v`

Expected: 2 passed.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/filings/ tests/unit/data/providers/test_filings_edgar_as_of.py`

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/data/providers/filings/edgar.py tests/unit/data/providers/test_filings_edgar_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): filings/edgar applies as_of upper bound to filing_date

_list_filings previously had no date filter at all — fetch returned the
latest filings regardless of as_of.  Add filing_date=":<as_of_iso>"
upper bound + thread as_of through fetch with **_unused.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Accept `as_of` in `news/finnhub.py`

**Files:**
- Modify: `src/data/providers/news/finnhub.py` (lines 25-69)
- Test: `tests/unit/data/providers/test_news_finnhub_as_of.py` (new)

**What & why:** `news/finnhub` already honours `from_date`/`to_date` correctly. It just needs to accept `as_of` (so dispatch doesn't `TypeError`) plus `**_unused`. No data-logic change.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_news_finnhub_as_of.py`:

```python
"""``news/finnhub.fetch`` accepts ``as_of`` without using it for data logic."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_accepts_as_of_kwarg(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` must accept ``as_of`` even though it relies on ``from_date``/``to_date``."""
    import data.providers.news.finnhub as mod

    captured: dict = {}

    def fake_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        captured["symbol"]   = symbol
        captured["from_iso"] = from_iso
        captured["to_iso"]   = to_iso
        return []

    monkeypatch.setattr(mod, "_fetch_company_news", fake_fetch)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert out == []
    assert captured["symbol"]   == "AAPL"
    assert captured["from_iso"] == "2023-03-01"
    assert captured["to_iso"]   == "2023-03-15"


@pytest.mark.asyncio
async def test_fetch_accepts_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``**_unused`` absorbs kwargs other providers consume."""
    import data.providers.news.finnhub as mod
    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: [])

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )
    assert out == []
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_news_finnhub_as_of.py -v`

Expected: FAIL — `fetch() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Update `fetch`**

Replace lines 32-44 of `src/data/providers/news/finnhub.py`:

```python
@register(
    domain="news",
    name="finnhub",
    upstream="finnhub",
    rate_per_minute=60,
    burst=30,
)
async def fetch(
    ticker: str,
    *,
    from_date: date,
    to_date: date,
    as_of: datetime,
    limit: int | None = 50,
    **_unused,
) -> list[NewsArticle]:
    """Recent news articles for ``ticker`` from Finnhub's ``company_news`` endpoint.

    ``as_of`` is accepted for signature parity with other domains' providers but
    Finnhub already filters by ``from_date``/``to_date`` so no additional logic
    is needed.
    """
    symbol = ticker.upper()
    raw    = await asyncio.to_thread(
        _fetch_company_news, symbol, from_date.isoformat(), to_date.isoformat()
    )
```

Add `from datetime import UTC, date, datetime` at the top — replace the existing line 5:

```python
from datetime import UTC, date, datetime
```

(Already imports `UTC` and `datetime`; just confirm `datetime` is present.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_news_finnhub_as_of.py -v`

Expected: 2 passed.

- [ ] **Step 5: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/news/ tests/unit/data/providers/test_news_finnhub_as_of.py`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/data/providers/news/finnhub.py tests/unit/data/providers/test_news_finnhub_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): news/finnhub accepts as_of kwarg

Signature-only fix — finnhub already honours from_date/to_date.
Adding as_of + **_unused prevents the TypeError that every live
dispatch would have hit (wrappers in src/data/__init__.py pass as_of).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Accept `as_of` in `social_sentiment/finnhub.py`

**Files:**
- Modify: `src/data/providers/social_sentiment/finnhub.py` (the `fetch` signature)
- Test: `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py` (new)

**What & why:** Premium-only endpoint that already soft-fails on the free tier. Just add `as_of` + `**_unused` to the signature for dispatch parity.

- [ ] **Step 1: Read the current `fetch` signature**

Run: `PYTHONPATH=src .venv/bin/python -c "import inspect; import data.providers.social_sentiment.finnhub as m; print(inspect.signature(m.fetch))"`

Expected output: `(ticker: str) -> data.models.sentiment.SocialSentiment`

- [ ] **Step 2: Write the failing test**

Create `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py`:

```python
"""``social_sentiment/finnhub.fetch`` accepts ``as_of`` without using it."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_accepts_as_of_kwarg() -> None:
    """``fetch`` must accept ``as_of`` and any extra kwargs from dispatch."""
    import data.providers.social_sentiment.finnhub as mod

    # No FINNHUB_API_KEY assumed — provider soft-fails to empty SocialSentiment.
    result = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=14,  # type: ignore[call-arg]
    )

    # Soft-fail contract — provider returns a non-exception value.
    assert result is not None
```

- [ ] **Step 3: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py -v`

Expected: FAIL — `fetch() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 4: Update `fetch`**

In `src/data/providers/social_sentiment/finnhub.py`, change the `async def fetch(ticker: str) -> SocialSentiment:` signature to:

```python
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    **_unused,
) -> SocialSentiment:
    """Reddit/Twitter sentiment snapshot for ``ticker`` from Finnhub.

    ``as_of`` is accepted for dispatch parity.  Finnhub's social sentiment
    endpoint is premium-only and soft-fails on the free tier; ``as_of`` is
    not used by the current implementation.
    """
```

Make sure `from datetime import datetime` is imported (add if missing).

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py -v`

Expected: 1 passed.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/social_sentiment/ tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py`

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/data/providers/social_sentiment/finnhub.py \
        tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): social_sentiment/finnhub accepts as_of kwarg

Signature-only fix for dispatch parity.  Endpoint is premium-only and
remains soft-fail on the free tier.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Accept `as_of` in `stats/yfinance.py`

**Files:**
- Modify: `src/data/providers/stats/yfinance.py` (both `fetch_price_history` and `fetch_company_ratios`)
- Test: `tests/unit/data/providers/test_stats_yfinance_as_of.py` (new)

**What & why:** Both yfinance providers register on the same module but neither accepts `as_of`. yfinance's API is fundamentally wall-clock-anchored ("now"), so live behaviour stays identical — `as_of` is accepted but unused. The backfill workflow slices yfinance's max-period history client-side (already done in `backtest_fetch.py:75`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_stats_yfinance_as_of.py`:

```python
"""yfinance providers accept ``as_of`` for dispatch parity (no data-logic change)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.models import CompanyRatios, PriceHistory


@pytest.mark.asyncio
async def test_fetch_price_history_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_price_history`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_price_history",
        lambda s, p, i: PriceHistory(ticker=s, bars=[]),
    )

    out = await mod.fetch_price_history(
        "AAPL",
        period="1y",
        interval="1d",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )

    assert isinstance(out, PriceHistory)
    assert out.ticker == "AAPL"


@pytest.mark.asyncio
async def test_fetch_company_ratios_accepts_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch_company_ratios`` must accept ``as_of`` + ``**_unused``."""
    import data.providers.stats.yfinance as mod

    monkeypatch.setattr(
        mod, "_fetch_company_ratios",
        lambda s, p, i: CompanyRatios(ticker=s),
    )

    out = await mod.fetch_company_ratios(
        "AAPL",
        period="1y",
        interval="1d",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )

    assert isinstance(out, CompanyRatios)
    assert out.ticker == "AAPL"
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_stats_yfinance_as_of.py -v`

Expected: FAIL on both tests — `unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Update `fetch_price_history`**

In `src/data/providers/stats/yfinance.py`, replace the signature at line 175:

```python
@register(
    domain="price_history",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch_price_history(
    ticker: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    as_of: datetime,
    **_unused,
) -> PriceHistory:
    """Async wrapper for price-history fetch.

    ``as_of`` is accepted for dispatch parity but yfinance's period queries are
    wall-clock anchored — live behaviour is unchanged.  Backfill callers slice
    the returned ``max``-period history client-side to the historical window
    (see ``scripts/backtest_fetch.py``).
    """
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_price_history, symbol, period, interval)
```

- [ ] **Step 4: Update `fetch_company_ratios`**

Replace the signature at line 205:

```python
@register(
    domain="company_ratios",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch_company_ratios(
    ticker: str,
    *,
    period: str = "1y",
    interval: str = "1d",
    as_of: datetime,
    **_unused,
) -> CompanyRatios:
    """Async wrapper for company-ratios fetch.

    ``as_of`` is accepted for dispatch parity; yfinance's ``info`` endpoint
    serves wall-clock-current data, so this provider is unsuitable for
    historical PIT queries.  Use the ``pit_composite`` provider (added in a
    later task) for backtests.
    """
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_company_ratios, symbol, period, interval)
```

Add `datetime` to the imports if not present (it is — the module already imports from `datetime`). Add an `if TYPE_CHECKING` or top-level `from datetime import datetime`:

```python
from datetime import datetime
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_stats_yfinance_as_of.py -v`

Expected: 2 passed.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/stats/ tests/unit/data/providers/test_stats_yfinance_as_of.py`

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/data/providers/stats/yfinance.py \
        tests/unit/data/providers/test_stats_yfinance_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): stats/yfinance accepts as_of kwarg

Signature-only fix for dispatch parity.  yfinance is wall-clock anchored;
PIT-correct ratios come from the pit_composite provider added later in
this branch.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Plumb `as_of` through `politician_trades/quiver.py`

**Files:**
- Modify: `src/data/providers/politician_trades/quiver.py` (lines 86-124)
- Test: `tests/unit/data/providers/test_politician_trades_quiver_as_of.py` (new)

**What & why:** Same bug as Task 1 — the cutoff is `date.today() - lookback`. Move it onto `as_of.date()`. Quiver stays registered as a fallback after we ship the FMP replacement, so this fix matters whenever the user re-enables Quiver.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_politician_trades_quiver_as_of.py`:

```python
"""``politician_trades/quiver.fetch`` honours ``as_of`` for the cutoff."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cutoff must be ``as_of - lookback``, not ``date.today() - lookback``."""
    import data.providers.politician_trades.quiver as mod

    # Force the soft-fail path so we don't need a real API key — but we still
    # want to see fetch happily accept the as_of kwarg.
    monkeypatch.delenv("QUIVER_QUANT_API_KEY", raising=False)

    out = await mod.fetch(
        "AAPL",
        lookback_days=90,
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    # Soft-fail returns [] when the API key is missing.
    assert out == []


@pytest.mark.asyncio
async def test_fetch_applies_as_of_cutoff_to_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a payload is returned, trades older than ``as_of - lookback`` must drop."""
    import data.providers.politician_trades.quiver as mod

    monkeypatch.setenv("QUIVER_QUANT_API_KEY", "fake-key")

    # Older than 90d from 2023-03-15 → must drop.
    # Inside window → must include.
    monkeypatch.setattr(mod, "_fetch_trades", lambda symbol, key: [
        {"TransactionDate": "2022-12-01", "Representative": "Old Trader", "Transaction": "Buy"},
        {"TransactionDate": "2023-02-10", "Representative": "Recent Trader", "Transaction": "Buy"},
    ])

    out = await mod.fetch(
        "AAPL",
        lookback_days=90,
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    names = [t.politician for t in out]
    assert "Recent Trader" in names
    assert "Old Trader"    not in names
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_politician_trades_quiver_as_of.py -v`

Expected: FAIL — `fetch() got an unexpected keyword argument 'as_of'`.

- [ ] **Step 3: Update `fetch`**

In `src/data/providers/politician_trades/quiver.py`, replace lines 80-124:

```python
@register(
    domain="politician_trades",
    name="quiver",
    upstream="quiver",
    rate_per_minute=30,
    burst=10,
)
async def fetch(
    ticker: str | None = None,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[PoliticianTrade]:
    """Congressional trades for ``ticker`` reported within ``(as_of - lookback_days, as_of]``.

    Anchored on ``as_of`` so backfill never returns trades that did not yet
    exist at the historical moment.  Soft-fails to ``[]`` when
    ``QUIVER_QUANT_API_KEY`` is unset (free tier unavailable).
    """
    api_key = os.getenv("QUIVER_QUANT_API_KEY")
    if not api_key:
        logger.debug("QUIVER_QUANT_API_KEY unset — fetch returning []")
        return []

    symbol  = ticker.upper() if ticker else None
    payload = await asyncio.to_thread(_fetch_trades, symbol, api_key)

    cutoff = as_of.date() - timedelta(days=lookback_days)
    upper  = as_of.date()
    trades: list[PoliticianTrade] = []
    for item in payload:
        txn_date = _parse_date(item.get("TransactionDate") or item.get("Traded"))
        if txn_date is None or txn_date <= cutoff or txn_date > upper:
            continue
        amount_min, amount_max = _parse_amount_range(
            item.get("Range") or item.get("Amount") or item.get("Trade_Size_USD")
        )
        trades.append(
            PoliticianTrade(
                ticker=(item.get("Ticker") or symbol or "").upper(),
                politician=item.get("Representative") or item.get("Senator") or item.get("Name") or "unknown",
                chamber=item.get("Chamber") or item.get("House") or None,
                party=item.get("Party"),
                side=_coerce_side(item.get("Transaction") or item.get("Type")),
                transaction_date=txn_date,
                disclosure_date=_parse_date(item.get("ReportDate") or item.get("Disclosed")),
                amount_min_usd=amount_min,
                amount_max_usd=amount_max,
            )
        )
    return trades
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_politician_trades_quiver_as_of.py -v`

Expected: 2 passed.

- [ ] **Step 5: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/politician_trades/ tests/unit/data/providers/test_politician_trades_quiver_as_of.py`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/data/providers/politician_trades/quiver.py \
        tests/unit/data/providers/test_politician_trades_quiver_as_of.py
git commit -m "$(cat <<'EOF'
fix(providers): politician_trades/quiver honours as_of for cutoff

Replace date.today() with as_of.date() so backfill sees only trades
disclosed at the historical moment.  Quiver stays registered as the
fallback after FMP becomes the active provider.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Regression sweep — confirm dispatch no longer TypeErrors

**Files:**
- Test: `tests/unit/data/test_dispatch_passes_as_of.py` (new)

**What & why:** Belt-and-braces test that calls every wrapper in `src/data/__init__.py` through the registry and asserts none raise `TypeError`. Guards the latent bug we just fixed from regressing.

- [ ] **Step 1: Write the test**

Create `tests/unit/data/test_dispatch_passes_as_of.py`:

```python
"""Every public wrapper must dispatch without TypeError now that as_of is plumbed.

Guards the bug where leaf providers ignored ``as_of`` while wrappers passed it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest


@pytest.mark.asyncio
async def test_get_insider_trades_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_insider_trades`` must not TypeError when calling the active provider."""
    import data.providers.insider_trades.edgar as mod
    from data import get_insider_trades

    monkeypatch.setattr(mod, "_list_form4_filings", lambda s, l, a: [])
    out = await get_insider_trades("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out.trades == []


@pytest.mark.asyncio
async def test_get_notable_holders_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.notable_holders.edgar as mod
    from data import get_notable_holders

    monkeypatch.setattr(mod, "_list_holder_filings", lambda s, l, lim, a: [])
    out = await get_notable_holders("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out == []


@pytest.mark.asyncio
async def test_get_company_filings_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.filings.edgar as mod
    from data import get_company_filings

    monkeypatch.setattr(mod, "_list_filings", lambda s, ft, lim, a: [])
    out = await get_company_filings(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        include_excerpts=False,
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_stock_news_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.news.finnhub as mod
    from data import get_stock_news

    monkeypatch.setattr(mod, "_fetch_company_news", lambda s, f, t: [])
    out = await get_stock_news(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )
    assert out == []


@pytest.mark.asyncio
async def test_get_public_figure_trades_dispatches_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    from data import get_public_figure_trades

    monkeypatch.delenv("QUIVER_QUANT_API_KEY", raising=False)
    out = await get_public_figure_trades("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out == []
```

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_dispatch_passes_as_of.py -v`

Expected: 5 passed.

- [ ] **Step 3: Run the full non-slow suite to confirm no regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: All previously passing tests still pass.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/data/test_dispatch_passes_as_of.py
git commit -m "$(cat <<'EOF'
test(data): regression guard that wrappers dispatch as_of cleanly

Calls every public wrapper through the registry and asserts no
TypeError — covers the bug where leaf providers ignored as_of while
wrappers passed it.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: New `news/tiingo.py` provider

**Files:**
- Create: `src/data/providers/news/tiingo.py`
- Create: `tests/unit/data/providers/test_news_tiingo.py`

**What & why:** Tiingo News gives 1000 articles/day per ticker on the free tier, with date-range support — perfect for backfill. Add it alongside the existing finnhub provider; config remains pointing at finnhub for this commit. The endpoint is `https://api.tiingo.com/tiingo/news?tickers=X&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD&token=...`. Soft-fail to `[]` when `TIINGO_API_KEY` is unset.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_news_tiingo.py`:

```python
"""``news/tiingo.fetch`` returns NewsArticles, soft-fails without an API key."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from data.models import NewsArticle


@pytest.mark.asyncio
async def test_tiingo_soft_fails_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``TIINGO_API_KEY`` must yield ``[]``, never raise."""
    import data.providers.news.tiingo as mod

    monkeypatch.delenv("TIINGO_API_KEY", raising=False)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert out == []


@pytest.mark.asyncio
async def test_tiingo_parses_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tiingo JSON rows map to ``NewsArticle`` objects with the right fields."""
    import data.providers.news.tiingo as mod

    monkeypatch.setenv("TIINGO_API_KEY", "fake-key")

    payload = [
        {
            "id":            123,
            "title":         "Apple unveils Vision Pro",
            "description":   "Cupertino reveals its mixed-reality headset.",
            "url":           "https://example.test/aapl-vision-pro",
            "publishedDate": "2023-03-10T12:00:00+00:00",
            "source":        "example.test",
            "tickers":       ["aapl"],
            "tags":          ["technology"],
        },
    ]

    monkeypatch.setattr(mod, "_fetch_news", lambda symbol, start, end, key, limit: payload)

    out = await mod.fetch(
        "AAPL",
        from_date=date(2023, 3, 1),
        to_date=date(2023, 3, 15),
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(out) == 1
    assert isinstance(out[0], NewsArticle)
    assert out[0].ticker      == "AAPL"
    assert out[0].headline    == "Apple unveils Vision Pro"
    assert out[0].source      == "example.test"
    assert out[0].url         == "https://example.test/aapl-vision-pro"


def test_tiingo_registers_on_import() -> None:
    """Importing the module registers the (news, tiingo) entry."""
    import data.providers.news.tiingo  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("news", "tiingo")]
    assert entry.upstream == "tiingo"
    assert _LIMITERS["tiingo"].rate_per_minute > 0
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_news_tiingo.py -v`

Expected: 3 failed — module not found.

- [ ] **Step 3: Create the Tiingo provider**

Create `src/data/providers/news/tiingo.py`:

```python
"""Tiingo News provider — historical news for backfill (free tier, 1000/day per ticker).

Endpoint:
    https://api.tiingo.com/tiingo/news?tickers=AAPL&startDate=2023-03-01&endDate=2023-03-15&token=...

Returns up to 1000 articles per call.  We pass ``startDate``/``endDate`` from
``from_date``/``to_date`` so backfill receives PIT-correct news.  Live callers
that omit those defaults to ``(as_of - 7d, as_of.date())`` via the wrapper.

Soft-fails to ``[]`` when ``TIINGO_API_KEY`` is unset so the live pipeline can
fall back to another news provider via config.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, date, datetime
from typing import Any

import requests

from data.registry import register
from data.retry import with_retry

from ...models import NewsArticle

logger = logging.getLogger(__name__)

_BASE_URL     = "https://api.tiingo.com/tiingo/news"
_HTTP_TIMEOUT = 15.0
_PAGE_LIMIT   = 1000  # free-tier per-call cap


def _parse_published(raw: Any) -> datetime:
    """Coerce Tiingo's ISO ``publishedDate`` into a timezone-aware ``datetime``."""
    if raw is None:
        return datetime.now(tz=UTC)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@with_retry
def _fetch_news(symbol: str, start: str, end: str, api_key: str, limit: int) -> list[dict]:
    """Hit the Tiingo News endpoint and return raw JSON rows."""
    params = {
        "tickers":   symbol,
        "startDate": start,
        "endDate":   end,
        "token":     api_key,
        "limit":     limit,
    }
    resp = requests.get(_BASE_URL, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


@register(
    domain="news",
    name="tiingo",
    upstream="tiingo",
    rate_per_minute=60,
    burst=20,
)
async def fetch(
    ticker: str,
    *,
    from_date: date,
    to_date: date,
    as_of: datetime,
    limit: int | None = 50,
    **_unused,
) -> list[NewsArticle]:
    """News articles for ``ticker`` published in ``[from_date, to_date]``.

    Tiingo applies the date filter server-side so we only need to project
    each row into a ``NewsArticle``.  Returns ``[]`` on missing API key.
    """
    api_key = os.getenv("TIINGO_API_KEY")
    if not api_key:
        logger.debug("TIINGO_API_KEY unset — fetch returning []")
        return []

    symbol     = ticker.upper()
    page_limit = limit or _PAGE_LIMIT
    rows = await asyncio.to_thread(
        _fetch_news,
        symbol,
        from_date.isoformat(),
        to_date.isoformat(),
        api_key,
        page_limit,
    )

    out: list[NewsArticle] = []
    for row in rows:
        out.append(
            NewsArticle(
                ticker=symbol,
                headline=row.get("title", "") or "",
                summary=row.get("description", "") or "",
                url=row.get("url", "") or "",
                source=row.get("source", "") or "",
                published_at=_parse_published(row.get("publishedDate")),
                sentiment=None,
            )
        )
    return out
```

- [ ] **Step 4: Register the new provider for import**

Append to `src/data/providers/news/__init__.py` (create if missing):

```bash
PYTHONPATH=src .venv/bin/python -c "from pathlib import Path; p = Path('src/data/providers/news/__init__.py'); print(p.read_text() if p.exists() else '(missing)')"
```

If `tiingo` is not imported there, add `from . import tiingo  # noqa: F401`. (Same convention as finnhub.) If `__init__.py` is empty or just docstring, add at end:

```python
from . import tiingo  # noqa: F401
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_news_tiingo.py -v`

Expected: 3 passed.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/news/ tests/unit/data/providers/test_news_tiingo.py`

Expected: `All checks passed!`

- [ ] **Step 7: Add `TIINGO_API_KEY` to the env documentation**

Append to `.env.example` (or equivalent — look for the existing pattern):

```bash
grep -l "FINNHUB_API_KEY" .env.example .env 2>/dev/null || true
```

If `.env.example` exists, add one line after the `FINNHUB_API_KEY` entry:

```
TIINGO_API_KEY=                # Tiingo News (free tier: 1000 articles/day/ticker)
```

If it doesn't exist, skip this step — the project tracks env vars in
`config/README.md`; add the same line there under any "API keys" section.

- [ ] **Step 8: Commit**

```bash
git add src/data/providers/news/tiingo.py \
        src/data/providers/news/__init__.py \
        tests/unit/data/providers/test_news_tiingo.py \
        .env.example config/README.md 2>/dev/null || true
git commit -m "$(cat <<'EOF'
feat(news): add Tiingo provider for PIT-correct historical news

Tiingo's /tiingo/news endpoint supports server-side startDate/endDate
filtering and gives 1000 articles/day/ticker on the free tier — ideal
for backfill.  Registered alongside finnhub; config unchanged.
Soft-fails to [] when TIINGO_API_KEY is unset.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: New `politician_trades/fmp.py` provider

**Files:**
- Create: `src/data/providers/politician_trades/fmp.py`
- Create: `tests/unit/data/providers/test_politician_trades_fmp.py`

**What & why:** Financial Modeling Prep gives `/senate-trading?symbol=X` and `/senate-disclosure?symbol=X` (250 calls/day free) — a free replacement for Quiver. Merge both endpoints into one provider call. Soft-fail to `[]` without `FMP_API_KEY`. Apply the same `as_of` cutoff Quiver uses.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_politician_trades_fmp.py`:

```python
"""``politician_trades/fmp.fetch`` merges senate + house feeds with PIT cutoff."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.models import PoliticianTrade


@pytest.mark.asyncio
async def test_fmp_soft_fails_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.politician_trades.fmp as mod
    monkeypatch.delenv("FMP_API_KEY", raising=False)

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))
    assert out == []


@pytest.mark.asyncio
async def test_fmp_merges_senate_and_house(monkeypatch: pytest.MonkeyPatch) -> None:
    import data.providers.politician_trades.fmp as mod
    monkeypatch.setenv("FMP_API_KEY", "fake")

    monkeypatch.setattr(mod, "_fetch_senate", lambda s, k: [
        {
            "transactionDate": "2023-02-20",
            "disclosureDate":  "2023-03-05",
            "firstName":       "Nancy",
            "lastName":        "Pelosi",
            "office":          "House",
            "owner":           "self",
            "type":            "Purchase",
            "amount":          "$15,001 - $50,000",
        },
    ])
    monkeypatch.setattr(mod, "_fetch_house", lambda s, k: [
        {
            "transactionDate": "2023-02-25",
            "disclosureDate":  "2023-03-07",
            "firstName":       "Tommy",
            "lastName":        "Tuberville",
            "office":          "Senate",
            "owner":           "self",
            "type":            "Sale",
            "amount":          "$50,001 - $100,000",
        },
    ])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=90,
    )

    assert len(out) == 2
    assert all(isinstance(t, PoliticianTrade) for t in out)
    sides = {t.side for t in out}
    assert sides == {"buy", "sell"}


@pytest.mark.asyncio
async def test_fmp_applies_as_of_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Trades disclosed after ``as_of`` must drop (lookahead protection)."""
    import data.providers.politician_trades.fmp as mod
    monkeypatch.setenv("FMP_API_KEY", "fake")

    monkeypatch.setattr(mod, "_fetch_senate", lambda s, k: [
        {
            "transactionDate": "2023-04-20",
            "disclosureDate":  "2023-04-25",
            "firstName":       "Future",
            "lastName":        "Trader",
            "office":          "Senate",
            "type":            "Purchase",
            "amount":          "$1,001 - $15,000",
        },
    ])
    monkeypatch.setattr(mod, "_fetch_house", lambda s, k: [])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=90,
    )
    assert out == []


def test_fmp_registers_on_import() -> None:
    import data.providers.politician_trades.fmp  # noqa: F401
    from data.registry import _LIMITERS, _REGISTRY

    entry = _REGISTRY[("politician_trades", "fmp")]
    assert entry.upstream == "fmp"
    assert _LIMITERS["fmp"].rate_per_minute > 0
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_politician_trades_fmp.py -v`

Expected: 4 failed — module not found.

- [ ] **Step 3: Create the FMP provider**

Create `src/data/providers/politician_trades/fmp.py`:

```python
"""FMP politician-trades provider — free 250/day, covers Senate + House.

Endpoints (FMP v4):
    https://financialmodelingprep.com/api/v4/senate-trading?symbol=AAPL&apikey=...
    https://financialmodelingprep.com/api/v4/senate-disclosure?symbol=AAPL&apikey=...

Both feeds use the same JSON row shape (``transactionDate``, ``disclosureDate``,
``firstName``, ``lastName``, ``office``, ``type``, ``amount``).  This provider
merges them, then applies the standard ``as_of`` cutoff + lookback window.

Soft-fails to ``[]`` when ``FMP_API_KEY`` is unset.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import requests

from data.registry import register
from data.retry import with_retry

from ...models import PoliticianTrade, TradeSide

logger = logging.getLogger(__name__)

_BASE_URL     = "https://financialmodelingprep.com/api/v4"
_HTTP_TIMEOUT = 15.0

_SIDE_MAP: dict[str, TradeSide] = {
    "purchase":         "buy",
    "buy":              "buy",
    "sale":             "sell",
    "sale (full)":      "sell",
    "sale (partial)":   "sell",
    "sell":             "sell",
    "exchange":         "exchange",
}


def _coerce_side(raw: Any) -> TradeSide:
    """Map FMP's ``type`` string to our ``TradeSide`` literal."""
    if not raw:
        return "unknown"
    return _SIDE_MAP.get(str(raw).strip().lower(), "unknown")


def _parse_date(raw: Any) -> date | None:
    """Coerce ``YYYY-MM-DD`` strings into ``date``; return ``None`` on failure."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def _parse_amount_range(raw: Any) -> tuple[float | None, float | None]:
    """Parse ``"$15,001 - $50,000"``-style amount strings into a numeric range."""
    if raw is None:
        return None, None
    text = str(raw).replace("$", "").replace(",", "").strip()
    if not text:
        return None, None
    parts = [p.strip() for p in text.split("-")]
    try:
        if len(parts) == 2:
            return float(parts[0]), float(parts[1])
        return float(parts[0]), float(parts[0])
    except ValueError:
        return None, None


@with_retry
def _fetch_senate(symbol: str, api_key: str) -> list[dict]:
    """Call FMP ``/senate-trading?symbol=...`` and return raw rows."""
    url    = f"{_BASE_URL}/senate-trading"
    params = {"symbol": symbol, "apikey": api_key}
    resp   = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


@with_retry
def _fetch_house(symbol: str, api_key: str) -> list[dict]:
    """Call FMP ``/senate-disclosure?symbol=...`` (covers House) and return raw rows."""
    url    = f"{_BASE_URL}/senate-disclosure"
    params = {"symbol": symbol, "apikey": api_key}
    resp   = requests.get(url, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


def _row_to_trade(row: dict, symbol: str) -> PoliticianTrade | None:
    """Project one FMP row into a ``PoliticianTrade``.  Returns ``None`` on missing date."""
    txn_date = _parse_date(row.get("transactionDate"))
    if txn_date is None:
        return None
    disclosure = _parse_date(row.get("disclosureDate"))
    amount_min, amount_max = _parse_amount_range(row.get("amount"))
    politician = " ".join(
        p for p in (row.get("firstName"), row.get("lastName")) if p
    ) or "unknown"
    return PoliticianTrade(
        ticker=symbol,
        politician=politician,
        chamber=row.get("office") or None,
        party=row.get("party") or None,
        side=_coerce_side(row.get("type")),
        transaction_date=txn_date,
        disclosure_date=disclosure,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
    )


@register(
    domain="politician_trades",
    name="fmp",
    upstream="fmp",
    rate_per_minute=20,
    burst=10,
)
async def fetch(
    ticker: str | None = None,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[PoliticianTrade]:
    """Senate + House trades for ``ticker`` filed in ``(as_of - lookback, as_of]``.

    Merges FMP's two endpoints into one list of ``PoliticianTrade``s.  Applies
    the same PIT cutoff as the cache reader (``COALESCE(disclosure, transaction)``).
    Soft-fails to ``[]`` when ``FMP_API_KEY`` is unset.
    """
    api_key = os.getenv("FMP_API_KEY")
    if not api_key:
        logger.debug("FMP_API_KEY unset — fetch returning []")
        return []

    symbol = (ticker or "").upper()
    if not symbol:
        return []

    senate, house = await asyncio.gather(
        asyncio.to_thread(_fetch_senate, symbol, api_key),
        asyncio.to_thread(_fetch_house,  symbol, api_key),
    )

    lower      = as_of.date() - timedelta(days=lookback_days)
    upper      = as_of.date()
    out: list[PoliticianTrade] = []
    for row in (*senate, *house):
        trade = _row_to_trade(row, symbol)
        if trade is None:
            continue
        pit = trade.disclosure_date or trade.transaction_date
        if pit <= lower or pit > upper:
            continue
        out.append(trade)
    return out
```

- [ ] **Step 4: Register the new provider for import**

Append to `src/data/providers/politician_trades/__init__.py`:

```python
from . import fmp  # noqa: F401
```

(Same pattern as the existing `from . import quiver`.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_politician_trades_fmp.py -v`

Expected: 4 passed.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/politician_trades/ tests/unit/data/providers/test_politician_trades_fmp.py`

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/data/providers/politician_trades/fmp.py \
        src/data/providers/politician_trades/__init__.py \
        tests/unit/data/providers/test_politician_trades_fmp.py
git commit -m "$(cat <<'EOF'
feat(politician_trades): add FMP provider (free 250/day, Senate + House)

Replaces paid Quiver for backfill: FMP /senate-trading +
/senate-disclosure cover both chambers and accept PIT-correct symbol
queries.  Registered alongside quiver; config unchanged.  Soft-fails
to [] without FMP_API_KEY.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: New `company_ratios/pit_composite.py` provider

**Files:**
- Create: `src/data/providers/company_ratios/__init__.py`
- Create: `src/data/providers/company_ratios/pit_composite.py`
- Create: `tests/unit/data/providers/test_company_ratios_pit_composite.py`

**What & why:** The current yfinance ratios provider serves wall-clock data via `Ticker.info`. Replace it with a composite that uses edgartools `EntityFacts.query().as_of(as_of.date())` for raw fundamentals + yfinance `period="max" interval="1d"` OHLCV (sliced to `as_of`) for price-derived fields. See spec §3 "Composite provider rationale".

- [ ] **Step 1: Write the failing test**

Create `tests/unit/data/providers/test_company_ratios_pit_composite.py`:

```python
"""PIT-composite ratios provider — XBRL fundamentals + sliced OHLCV technicals."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from data.models import CompanyRatios, OHLCBar, PriceHistory


def _make_bars(n: int, last_close: float = 175.0) -> list[OHLCBar]:
    """Create ``n`` daily bars ending at 2023-03-14 with ``last_close``."""
    bars: list[OHLCBar] = []
    for i in range(n):
        ts    = datetime(2023, 1, 1, tzinfo=UTC).replace(day=min(i + 1, 28))
        close = last_close - (n - 1 - i) * 0.5
        bars.append(OHLCBar(
            timestamp=ts,
            open=close - 0.5,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            volume=1_000_000.0,
        ))
    return bars


@pytest.mark.asyncio
async def test_pit_composite_returns_filled_ratios(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider composes XBRL fundamentals + price-derived technicals."""
    import data.providers.company_ratios.pit_composite as mod

    fake_facts = SimpleNamespace(
        long_name      = "Apple Inc.",
        sector         = "Technology",
        shares_out     = 15_700_000_000.0,
        eps_ttm        = 6.0,
        dps_ttm        = 0.92,
    )
    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda symbol, as_of_date: fake_facts)

    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda symbol, as_of: PriceHistory(ticker=symbol, bars=_make_bars(220, last_close=175.0)),
    )

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.long_name      == "Apple Inc."
    assert out.sector         == "Technology"
    assert out.last_price     == pytest.approx(175.0)
    assert out.market_cap     == pytest.approx(15_700_000_000.0 * 175.0)
    assert out.trailing_pe    == pytest.approx(175.0 / 6.0)
    assert out.dividend_yield == pytest.approx(0.92 / 175.0)
    assert out.fifty_day_average is not None
    assert out.two_hundred_day_average is not None


@pytest.mark.asyncio
async def test_pit_composite_handles_missing_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty XBRL must yield a model with ``None`` fundamentals, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name=None, sector=None, shares_out=None, eps_ttm=None, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=_make_bars(5, last_close=100.0)),
    )

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert isinstance(out, CompanyRatios)
    assert out.last_price  == pytest.approx(100.0)
    assert out.market_cap  is None
    assert out.trailing_pe is None


@pytest.mark.asyncio
async def test_pit_composite_handles_empty_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty OHLCV must yield ``None`` price-derived fields, not raise."""
    import data.providers.company_ratios.pit_composite as mod

    monkeypatch.setattr(mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="X Co", sector="X", shares_out=1.0, eps_ttm=1.0, dps_ttm=None,
    ))
    monkeypatch.setattr(
        mod, "_fetch_price_series",
        lambda s, a: PriceHistory(ticker=s, bars=[]),
    )

    out = await mod.fetch("XYZ", as_of=datetime(2023, 3, 14, tzinfo=UTC))

    assert out.long_name  == "X Co"
    assert out.last_price is None
    assert out.market_cap is None


def test_pit_composite_registers_on_import() -> None:
    import data.providers.company_ratios.pit_composite  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("company_ratios", "pit_composite")]
    assert entry.upstream == "yfinance"   # shares yfinance limiter for price data
```

- [ ] **Step 2: Run the test to verify failure**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_company_ratios_pit_composite.py -v`

Expected: 4 failed — module not found.

- [ ] **Step 3: Create the module skeleton**

```bash
mkdir -p src/data/providers/company_ratios
```

Create `src/data/providers/company_ratios/__init__.py`:

```python
"""Composite ``company_ratios`` providers — XBRL fundamentals + price slice."""
from . import pit_composite  # noqa: F401
```

- [ ] **Step 4: Implement the composite provider**

Create `src/data/providers/company_ratios/pit_composite.py`:

```python
"""PIT-correct ``company_ratios`` — edgartools XBRL fundamentals + yfinance OHLCV.

The ``CompanyRatios`` model carries three classes of field:

- **Identity** (``long_name``, ``sector``) — from XBRL submission metadata.
- **Raw fundamentals** (shares_out, eps_ttm, dps_ttm — implicit via
  ``trailing_pe``, ``dividend_yield``) — from XBRL ``EntityFacts.query().as_of``.
- **Price-dependent / technical** (``last_price``, ``market_cap``,
  ``trailing_pe``, ``dividend_yield``, ``fifty_day_average``,
  ``two_hundred_day_average``) — derived from yfinance OHLCV history sliced
  to ``as_of``.

Live behaviour: when ``as_of`` is "now" (the wrapper default), this reduces to
"use today's OHLCV close + latest XBRL facts" — identical signal to the old
yfinance provider, just with authoritative SEC fundamentals.
"""
from __future__ import annotations

import asyncio
import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import yfinance as yf
from edgar import Company, set_identity

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import CompanyRatios, OHLCBar, PriceHistory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Facts:
    """Subset of XBRL facts the composite needs."""

    long_name:   str | None
    sector:      str | None
    shares_out:  float | None
    eps_ttm:     float | None
    dps_ttm:     float | None


def _ensure_identity() -> None:
    """Set the EDGAR User-Agent identity (required by SEC)."""
    set_identity(require_key("EDGAR_IDENTITY"))


def _safe_float(v: Any) -> float | None:
    """Coerce ``v`` to a finite float; return ``None`` on failure."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    import math
    return f if math.isfinite(f) else None


@with_retry
def _fetch_xbrl_facts(symbol: str, as_of_date: date) -> _Facts:
    """Pull the snapshot of fundamentals known at ``as_of_date`` for ``symbol``.

    Uses edgartools ``EntityFacts.query().as_of(as_of_date)``.  Missing facts
    are ``None``; callers cope.
    """
    _ensure_identity()
    company = Company(symbol)
    facts   = company.get_facts()

    def _scalar(concept: str) -> float | None:
        """Look up a single XBRL concept value as of ``as_of_date``."""
        try:
            q = facts.query().by_concept(concept).as_of(as_of_date)
            row = q.latest() if hasattr(q, "latest") else None
            return _safe_float(getattr(row, "value", None)) if row else None
        except Exception:
            return None

    # us-gaap concepts.
    eps   = _scalar("EarningsPerShareBasic") or _scalar("EarningsPerShareDiluted")
    dps   = _scalar("CommonStockDividendsPerShareDeclared")
    shrs  = _scalar("CommonStockSharesOutstanding") or _scalar("EntityCommonStockSharesOutstanding")

    # Identity comes from the company entity, not a fact.
    long_name = getattr(company, "name", None) or getattr(company, "company_name", None)
    sector    = getattr(company, "sic_description", None) or getattr(company, "sector", None)

    return _Facts(
        long_name  = str(long_name) if long_name else None,
        sector     = str(sector)    if sector    else None,
        shares_out = shrs,
        eps_ttm    = eps,
        dps_ttm    = dps,
    )


@with_retry
def _fetch_price_series(symbol: str, as_of: datetime) -> PriceHistory:
    """Pull yfinance ``period="max"`` daily history and slice to ``as_of``.

    Returns a ``PriceHistory`` whose ``bars`` end on the most-recent trading
    day at or before ``as_of.date()``.  Empty bars list if yfinance returns
    nothing for the ticker.
    """
    ticker = yf.Ticker(symbol)
    df     = ticker.history(period="max", interval="1d", auto_adjust=True)
    bars: list[OHLCBar] = []
    if df is not None and not df.empty:
        cutoff = as_of.date()
        for ts, row in df.iterrows():
            bar_date = ts.date() if hasattr(ts, "date") else ts
            if bar_date > cutoff:
                continue
            bars.append(OHLCBar(
                timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row.get("Volume", 0) or 0),
            ))
    return PriceHistory(ticker=symbol, bars=bars)


def _moving_average(closes: list[float], window: int) -> float | None:
    """Mean of the last ``window`` closes, or ``None`` when fewer than ``window`` bars."""
    if len(closes) < window:
        return None
    return statistics.fmean(closes[-window:])


def _ratios_from_components(
    symbol:   str,
    facts:    _Facts,
    history:  PriceHistory,
) -> CompanyRatios:
    """Combine XBRL ``_Facts`` + sliced ``PriceHistory`` into a ``CompanyRatios``."""
    closes  = [b.close for b in history.bars]
    last    = closes[-1] if closes else None

    market_cap     = (
        facts.shares_out * last
        if facts.shares_out is not None and last is not None
        else None
    )
    trailing_pe    = (
        last / facts.eps_ttm
        if last is not None and facts.eps_ttm not in (None, 0)
        else None
    )
    dividend_yield = (
        facts.dps_ttm / last
        if last is not None and facts.dps_ttm is not None and last != 0
        else None
    )
    fifty_day      = _moving_average(closes,  50)
    two_hundred    = _moving_average(closes, 200)

    return CompanyRatios(
        ticker                  = symbol,
        long_name               = facts.long_name,
        sector                  = facts.sector,
        market_cap              = market_cap,
        trailing_pe             = trailing_pe,
        forward_pe              = None,  # not in XBRL; requires analyst estimates.
        beta                    = None,  # deferred (1y SPY correlation — future work).
        dividend_yield          = dividend_yield,
        fifty_day_average       = fifty_day,
        two_hundred_day_average = two_hundred,
        last_price              = last,
    )


# Upstream is "yfinance" because price fetching is the dominant rate-limited
# call here — the EDGAR call uses the edgar limiter via _ensure_identity inside
# _fetch_xbrl_facts (no token acquisition needed for `Company.get_facts`).
@register(
    domain="company_ratios",
    name="pit_composite",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    **_unused,
) -> CompanyRatios:
    """PIT-correct ``CompanyRatios`` snapshot for ``ticker`` at ``as_of``.

    ``period`` / ``interval`` are accepted for signature parity with the
    existing yfinance provider but the composite always slices ``period="max"``
    daily bars itself.
    """
    symbol = ticker.upper()
    facts, history = await asyncio.gather(
        asyncio.to_thread(_fetch_xbrl_facts,   symbol, as_of.date()),
        asyncio.to_thread(_fetch_price_series, symbol, as_of),
    )
    return _ratios_from_components(symbol, facts, history)
```

- [ ] **Step 5: Register the directory as a package**

The directory has `__init__.py` from Step 3, so it's already a package.  Confirm `src/data/providers/__init__.py` imports the new package — find the existing `from . import news, filings, ...` pattern and add `company_ratios` if not already there:

```bash
grep -n "from . import" src/data/providers/__init__.py
```

If `company_ratios` is missing, add it. (The wrapper layer in `src/data/__init__.py` already calls `_validate_active_providers_are_registered`, which will fail loudly on import if the registration didn't happen.)

- [ ] **Step 6: Run the test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/providers/test_company_ratios_pit_composite.py -v`

Expected: 4 passed.

- [ ] **Step 7: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/company_ratios/ tests/unit/data/providers/test_company_ratios_pit_composite.py`

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add src/data/providers/company_ratios/ \
        src/data/providers/__init__.py \
        tests/unit/data/providers/test_company_ratios_pit_composite.py
git commit -m "$(cat <<'EOF'
feat(company_ratios): PIT-composite provider (XBRL + sliced yfinance OHLCV)

Replaces yfinance.info-based ratios for backfill: edgartools EntityFacts
.query().as_of() gives raw fundamentals; yfinance period=max daily bars
sliced to as_of give price-derived fields (last_price, market_cap,
trailing_pe, dividend_yield, 50d/200d MA).  Registered alongside
yfinance; config unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Switch active providers in `config/data.json`

**Files:**
- Modify: `config/data.json`
- Modify: `config/README.md`

**What & why:** With Tiingo, FMP, and pit_composite all registered, flip the live data surface to use them. Reverting this file is a complete rollback — no code changes.

- [ ] **Step 1: Read current config**

Run: `cat config/data.json`

Expected output (sample):
```json
{
  "providers": {
    "price_history":     "yfinance",
    "company_ratios":    "yfinance",
    "news":              "finnhub",
    "social_sentiment":  "finnhub",
    "insider_trades":    "edgar",
    "politician_trades": "quiver",
    "notable_holders":   "edgar",
    "filings":           "edgar"
  }
}
```

- [ ] **Step 2: Edit `config/data.json`**

Update three values:

```json
{
  "providers": {
    "price_history":     "yfinance",
    "company_ratios":    "pit_composite",
    "news":              "tiingo",
    "social_sentiment":  "finnhub",
    "insider_trades":    "edgar",
    "politician_trades": "fmp",
    "notable_holders":   "edgar",
    "filings":           "edgar"
  }
}
```

(Preserve any other keys present in the actual file.)

- [ ] **Step 3: Update `config/README.md`**

Find the `data.json` documentation section and update the entries for
`company_ratios`, `news`, and `politician_trades` to list the new active
providers and the registered fallbacks. Example wording:

```markdown
- `company_ratios` (active: `pit_composite`, fallback: `yfinance`) — XBRL
  fundamentals via edgartools + sliced yfinance OHLCV for price-derived
  technicals. PIT-correct.
- `news` (active: `tiingo`, fallback: `finnhub`) — Tiingo News API
  (1000 articles/day/ticker free) with server-side date filtering.
- `politician_trades` (active: `fmp`, fallback: `quiver`) — Financial
  Modeling Prep `/senate-trading` + `/senate-disclosure` (free 250/day).
```

- [ ] **Step 4: Run the full non-slow suite to confirm registration is satisfied**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: all previously passing tests still pass. The data layer's
`_validate_active_providers_are_registered()` will fail at import time if any
swapped name is missing.

- [ ] **Step 5: Commit**

```bash
git add config/data.json config/README.md
git commit -m "$(cat <<'EOF'
chore(config): switch active providers for backtest-readiness

news        finnhub -> tiingo
company_ratios yfinance -> pit_composite
politician_trades quiver -> fmp

Fallback providers stay registered; reverting this file is a complete
rollback with zero code changes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Refactor `scripts/backtest_fetch.py` to use the public wrappers

**Files:**
- Modify: `scripts/backtest_fetch.py` (drop `_build_provider_fns`; call `data.get_*` directly)
- Test: existing `tests/integration/backtest/test_end_to_end_smoke.py` must continue to pass

**What & why:** Now that leaf providers honour `as_of`, the inline factory in `backtest_fetch.py` is dead weight. Use the public wrappers directly so backfill automatically picks up whichever provider is active in `config/data.json`. Add a `_fill_quarterly_ratios` helper that returns `list[(snapshot, quarter_end_date)]` so ratios get multiple historical snapshots inside the window.

- [ ] **Step 1: Read the current script**

Run: `cat scripts/backtest_fetch.py | wc -l` to confirm the file shape.

Expected: ~265 lines.

- [ ] **Step 2: Rewrite `_build_provider_fns` to call the wrappers**

Replace the body of `_build_provider_fns()` in `scripts/backtest_fetch.py` (the function defined around line 48) with:

```python
def _build_provider_fns() -> dict:
    """Return the domain → public-wrapper fetch-function map for the Fetcher.

    Each function has the signature ``async fn(ticker, *, start, end)`` and
    delegates to the matching ``data.get_*`` wrapper.  Whatever provider is
    active in ``config/data.json`` is used automatically — switching is a
    config-only operation.

    Returns
    -------
    dict[str, Callable]
        Keys mirror ``CachedDataStore`` writer domains.
    """
    from data import (
        get_company_filings,
        get_insider_trades,
        get_notable_holders,
        get_price_history,
        get_public_figure_trades,
        get_stock_news,
    )

    def _as_of_close(end) -> datetime:
        """Market-close datetime on ``end`` in New York time (matches live ticks)."""
        return datetime.combine(end, time(16, 0), tzinfo=_NY)

    async def _ohlcv(ticker: str, *, start, end) -> list:
        """Pull max-period history through the active price-history provider, then slice."""
        history = await get_price_history(
            ticker, period="max", interval="1d", as_of=_as_of_close(end),
        )
        return [bar for bar in history.bars if start <= bar.timestamp.date() <= end]

    async def _company_ratios(ticker: str, *, start, end) -> list:
        """Fan out quarter-end as_ofs across the window for PIT-correct snapshots."""
        return await _fill_quarterly_ratios(ticker, start, end)

    async def _news(ticker: str, *, start, end) -> list:
        return await get_stock_news(
            ticker, from_date=start, to_date=end, as_of=_as_of_close(end),
        )

    async def _filings(ticker: str, *, start, end) -> list:
        return await get_company_filings(ticker, as_of=_as_of_close(end))

    async def _insider_trades(ticker: str, *, start, end) -> list:
        lookback = (end - start).days + 14
        return await get_insider_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )

    async def _politician_trades(ticker: str, *, start, end) -> list:
        lookback = (end - start).days + 14
        return await get_public_figure_trades(
            ticker, lookback_days=lookback, as_of=_as_of_close(end),
        )

    async def _notable_holders(ticker: str, *, start, end) -> list:
        return await get_notable_holders(ticker, as_of=_as_of_close(end))

    return {
        "ohlcv":             _ohlcv,
        "company_ratios":    _company_ratios,
        "news":              _news,
        "filings":           _filings,
        "insider_trades":    _insider_trades,
        "politician_trades": _politician_trades,
        "notable_holders":   _notable_holders,
    }
```

- [ ] **Step 3: Add `_fill_quarterly_ratios`**

Add this module-level async function above `_build_provider_fns` in
`scripts/backtest_fetch.py`:

```python
async def _fill_quarterly_ratios(ticker: str, start, end) -> list:
    """Fetch one ``CompanyRatios`` snapshot per quarter-end in ``[start, end]``.

    Calls the active company_ratios provider once per quarter-end date and
    returns ``list[(snapshot, quarter_end_date)]`` so ``Fetcher._fetch_one``
    can unpack each tuple into the store's ``write_company_ratios`` signature.

    The replay reader uses ``as_of_date <= as_of`` so multiple snapshots
    inside one window let the analyst see the right quarter's fundamentals
    rather than a single window-end snapshot.
    """
    from datetime import date as _date

    from data import get_company_ratios

    # Calendar quarter-end dates: 31-Mar, 30-Jun, 30-Sep, 31-Dec.
    _Q_ENDS = ((3, 31), (6, 30), (9, 30), (12, 31))

    candidates: list[_date] = []
    for year in range(start.year, end.year + 1):
        for month, day in _Q_ENDS:
            candidates.append(_date(year, month, day))

    targets = [d for d in candidates if start <= d <= end]
    if not targets:
        # Window doesn't span any quarter-end — fall back to a single snapshot
        # at window-end so the cache is not entirely empty.
        targets = [end]

    out: list = []
    for qe in targets:
        snapshot = await get_company_ratios(
            ticker,
            period="max",
            interval="1d",
            as_of=datetime.combine(qe, time(16, 0), tzinfo=_NY),
        )
        out.append((snapshot, qe))
    return out
```

- [ ] **Step 4: Run the existing end-to-end smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`

Expected: passes (the smoke test mocks LLM calls and yfinance, so this run
exercises the new backfill wiring end-to-end).

- [ ] **Step 5: Run the rest of the non-slow suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`

Expected: all previously passing tests still pass.

- [ ] **Step 6: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check scripts/backtest_fetch.py`

Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add scripts/backtest_fetch.py
git commit -m "$(cat <<'EOF'
feat(backtest_fetch): backfill via public wrappers + quarterly ratios

Drop the inline _build_provider_fns factory and call the public data.*
wrappers directly.  Now the backfill follows config/data.json — flipping
a provider in config flips the source the backfill uses.  Add
_fill_quarterly_ratios so company_ratios gets one snapshot per quarter-end
in the window, matching what the analyst sees between earnings.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 14: Provider-swap regression test

**Files:**
- Test: `tests/unit/data/test_provider_switching.py` (new)

**What & why:** Lock in the "one config flip" requirement so a future contributor can't accidentally regress it. Test that the registry honours `cfg.providers` at dispatch time and that swapping the name actually changes which coroutine runs.

- [ ] **Step 1: Write the test**

Create `tests/unit/data/test_provider_switching.py`:

```python
"""Switching ``config/data.json`` providers must require zero code changes.

This is the regression guard for the "one config flip" feedback rule.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest


@pytest.mark.asyncio
async def test_news_swap_finnhub_to_tiingo_uses_tiingo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Setting providers[news] to 'tiingo' must route dispatch to the Tiingo coroutine."""
    import data.providers.news.tiingo as tiingo_mod
    from data import _dispatch
    from data.config import get_config

    monkeypatch.setenv("TIINGO_API_KEY", "fake")

    # Force the cache out of any prior tests.
    cfg = get_config()
    original = cfg.providers["news"]
    cfg.providers["news"] = "tiingo"

    called: dict = {"who": None}

    def fake_tiingo_fetch(symbol: str, start: str, end: str, key: str, limit: int) -> list:
        called["who"] = "tiingo"
        return []

    monkeypatch.setattr(tiingo_mod, "_fetch_news", fake_tiingo_fetch)

    try:
        await _dispatch(
            "news", "AAPL",
            from_date=datetime(2023, 3, 1, tzinfo=UTC).date(),
            to_date=datetime(2023, 3, 15, tzinfo=UTC).date(),
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
        )
    finally:
        cfg.providers["news"] = original

    assert called["who"] == "tiingo"


@pytest.mark.asyncio
async def test_news_swap_back_to_finnhub_uses_finnhub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flipping back to 'finnhub' routes to the Finnhub coroutine — no code change."""
    import data.providers.news.finnhub as finnhub_mod
    from data import _dispatch
    from data.config import get_config

    cfg = get_config()
    original = cfg.providers["news"]
    cfg.providers["news"] = "finnhub"

    called: dict = {"who": None}

    def fake_finnhub_fetch(symbol: str, from_iso: str, to_iso: str) -> list:
        called["who"] = "finnhub"
        return []

    monkeypatch.setattr(finnhub_mod, "_fetch_company_news", fake_finnhub_fetch)

    try:
        await _dispatch(
            "news", "AAPL",
            from_date=datetime(2023, 3, 1, tzinfo=UTC).date(),
            to_date=datetime(2023, 3, 15, tzinfo=UTC).date(),
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
        )
    finally:
        cfg.providers["news"] = original

    assert called["who"] == "finnhub"


@pytest.mark.asyncio
async def test_politician_trades_swap_fmp_to_quiver(monkeypatch: pytest.MonkeyPatch) -> None:
    """``politician_trades`` flips between fmp and quiver via config only."""
    import data.providers.politician_trades.quiver as quiver_mod
    from data import _dispatch
    from data.config import get_config

    cfg = get_config()
    original = cfg.providers["politician_trades"]
    cfg.providers["politician_trades"] = "quiver"
    monkeypatch.setenv("QUIVER_QUANT_API_KEY", "fake")

    called: dict = {"who": None}

    def fake_quiver_fetch(symbol, key) -> list:
        called["who"] = "quiver"
        return []

    monkeypatch.setattr(quiver_mod, "_fetch_trades", fake_quiver_fetch)

    try:
        await _dispatch(
            "politician_trades", "AAPL",
            as_of=datetime(2023, 3, 15, tzinfo=UTC),
            lookback_days=30,
        )
    finally:
        cfg.providers["politician_trades"] = original

    assert called["who"] == "quiver"
```

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_provider_switching.py -v`

Expected: 3 passed.

- [ ] **Step 3: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check tests/unit/data/test_provider_switching.py`

Expected: `All checks passed!`

- [ ] **Step 4: Commit**

```bash
git add tests/unit/data/test_provider_switching.py
git commit -m "$(cat <<'EOF'
test(data): regression guard for one-config-flip provider switching

Asserts that swapping config/data.json provider names actually routes
dispatch to the correct coroutine — protects the rule that swapping
providers (live or backtest) must never require a code change.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 15: Backfill smoke integration test

**Files:**
- Test: `tests/integration/backtest/test_backfill_smoke.py` (new)

**What & why:** End-to-end test that exercises the refactored `scripts/backtest_fetch.py` against a temp cache. Marked `@pytest.mark.slow` and `@pytest.mark.integration` so it stays out of the default suite. Monkeypatches the upstream HTTP calls so it runs offline. Confirms idempotency (re-run = zero new fetches).

- [ ] **Step 1: Write the test**

Create `tests/integration/backtest/test_backfill_smoke.py`:

```python
"""End-to-end backfill smoke: scripts.backtest_fetch fills a temp cache PIT-correctly.

Runs entirely offline by monkeypatching every leaf provider's inner HTTP/edgar
helper.  Re-running on the same cache must produce zero new fetches
(idempotency via cache_runs.status='ok').
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]


@pytest.mark.asyncio
async def test_backfill_writes_then_skips_on_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """One backfill run populates every domain; second run is fully idempotent."""
    # ── Arrange: temp cache + window + watchlist ─────────────────────────────
    cache_path     = tmp_path / "cache.sqlite"
    settings_path  = tmp_path / "backtest_settings.json"
    windows_path   = tmp_path / "backtest_windows.json"
    watchlist_path = tmp_path / "watchlist.json"

    settings_path.write_text(json.dumps({
        "cache_path":  str(cache_path),
        "runs_root":   str(tmp_path / "runs"),
    }))
    windows_path.write_text(json.dumps({
        "smoke": {"start": "2023-03-06", "end": "2023-03-10", "notes": "smoke"},
    }))
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL"]}))

    # ── Stub every leaf provider's inner fetch ──────────────────────────────
    from data.models import (
        CompanyRatios,
        Filing,
        InsiderTrade,
        NewsArticle,
        NotableHolder,
        OHLCBar,
        PriceHistory,
    )

    # OHLCV — return one bar inside the window.
    import data.providers.stats.yfinance as yf_mod
    monkeypatch.setattr(
        yf_mod, "_fetch_price_history",
        lambda s, p, i: PriceHistory(ticker=s, bars=[
            OHLCBar(
                timestamp=datetime(2023, 3, 8, tzinfo=UTC),
                open=170.0, high=175.0, low=168.0, close=173.0, volume=1.0,
            ),
        ]),
    )

    # company_ratios pit_composite — fake XBRL + price series.
    import data.providers.company_ratios.pit_composite as pit_mod
    from types import SimpleNamespace
    monkeypatch.setattr(pit_mod, "_fetch_xbrl_facts", lambda s, d: SimpleNamespace(
        long_name="Apple Inc.", sector="Technology",
        shares_out=15.7e9, eps_ttm=6.0, dps_ttm=0.92,
    ))
    monkeypatch.setattr(pit_mod, "_fetch_price_series", lambda s, a: PriceHistory(
        ticker=s, bars=[OHLCBar(
            timestamp=datetime(2023, 3, 8, tzinfo=UTC),
            open=170.0, high=175.0, low=168.0, close=173.0, volume=1.0,
        )],
    ))

    # news Tiingo
    monkeypatch.setenv("TIINGO_API_KEY", "fake")
    import data.providers.news.tiingo as tiingo_mod
    monkeypatch.setattr(tiingo_mod, "_fetch_news", lambda symbol, start, end, key, limit: [
        {
            "title":         "Apple news",
            "description":   "Body.",
            "url":           "https://example.test/article",
            "publishedDate": "2023-03-08T12:00:00+00:00",
            "source":        "example",
        },
    ])

    # politician_trades FMP
    monkeypatch.setenv("FMP_API_KEY", "fake")
    import data.providers.politician_trades.fmp as fmp_mod
    monkeypatch.setattr(fmp_mod, "_fetch_senate", lambda s, k: [{
        "transactionDate": "2023-03-07", "disclosureDate": "2023-03-09",
        "firstName": "Nancy", "lastName": "Pelosi", "office": "House",
        "type": "Purchase", "amount": "$15,001 - $50,000",
    }])
    monkeypatch.setattr(fmp_mod, "_fetch_house",  lambda s, k: [])

    # insider_trades + notable_holders + filings — empty lists are fine.
    import data.providers.insider_trades.edgar as ins_mod
    import data.providers.notable_holders.edgar as nh_mod
    import data.providers.filings.edgar as fl_mod
    monkeypatch.setattr(ins_mod, "_list_form4_filings", lambda s, l, a: [])
    monkeypatch.setattr(nh_mod,  "_list_holder_filings", lambda s, l, lim, a: [])
    monkeypatch.setattr(fl_mod,  "_list_filings",         lambda s, ft, lim, a: [])

    # ── Act 1: run the backfill ─────────────────────────────────────────────
    from scripts import backtest_fetch

    args = argparse.Namespace(
        window="smoke",
        watchlist=str(watchlist_path),
    )

    # The script reads paths from working-dir conventions — monkeypatch the
    # paths it opens so it picks up our temp configs.
    monkeypatch.chdir(tmp_path)
    Path("config").mkdir()
    Path("config/backtest_settings.json").write_text(settings_path.read_text())
    Path("config/backtest_windows.json").write_text(windows_path.read_text())

    await backtest_fetch._main_async(args)

    # ── Assert 1: cache has rows for every domain ──────────────────────────
    from backtest.cache.store import CachedDataStore
    store = CachedDataStore(cache_path)
    end_dt = datetime(2023, 3, 10, 16, 0, tzinfo=UTC)

    assert len(store.read_ohlcv("AAPL", date(2023, 3, 6), date(2023, 3, 10))) == 1
    ratios = store.read_company_ratios("AAPL", end_dt)
    assert isinstance(ratios, CompanyRatios)
    assert ratios.long_name == "Apple Inc."
    assert len(store.read_news("AAPL", end_dt, lookback_days=30)) == 1
    assert len(store.read_politician_trades("AAPL", end_dt, lookback_days=90)) == 1

    # ── Act 2: re-run with the same arguments ──────────────────────────────
    # Use a sentinel to assert no provider was re-called.
    called_again: dict = {"ohlcv": 0, "news": 0, "fmp_senate": 0}

    def _trip_ohlcv(*_a, **_kw):
        called_again["ohlcv"] += 1
        return PriceHistory(ticker="AAPL", bars=[])

    def _trip_news(*_a, **_kw):
        called_again["news"] += 1
        return []

    def _trip_fmp(*_a, **_kw):
        called_again["fmp_senate"] += 1
        return []

    monkeypatch.setattr(yf_mod,     "_fetch_price_history", _trip_ohlcv)
    monkeypatch.setattr(tiingo_mod, "_fetch_news",          _trip_news)
    monkeypatch.setattr(fmp_mod,    "_fetch_senate",        _trip_fmp)

    await backtest_fetch._main_async(args)

    # ── Assert 2: zero new fetches ─────────────────────────────────────────
    assert called_again == {"ohlcv": 0, "news": 0, "fmp_senate": 0}
```

- [ ] **Step 2: Run the new smoke test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_backfill_smoke.py -v -m slow`

Expected: 1 passed.

- [ ] **Step 3: Run the full slow/integration suite to confirm no neighbour regressions**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/ -v -m slow`

Expected: all backtest integration tests pass.

- [ ] **Step 4: Lint**

Run: `PYTHONPATH=src .venv/bin/python -m ruff check tests/integration/backtest/test_backfill_smoke.py`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add tests/integration/backtest/test_backfill_smoke.py
git commit -m "$(cat <<'EOF'
test(integration): backfill smoke + idempotency for scripts.backtest_fetch

End-to-end backfill against a temp cache, all upstreams mocked so it
runs offline.  Asserts the second run produces zero new fetches
(cache_runs.status='ok' skip path).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Self-review summary

**Spec coverage check:**

| Spec section | Tasks |
|---|---|
| §3 "Critical latent bug" (as_of TypeError on every wrapper) | 1, 2, 3, 4, 5, 6, 7, 8 |
| §3 row #7 (`pit_composite` ratios) | 11 |
| §3 row #8 (FMP politician_trades) | 10 |
| §3 row #9 (Tiingo news) | 9 |
| §3 row #10 (Quiver patch) | 7 |
| §3 "Composite provider rationale" | 11 |
| §3 "Plumbing pattern" (`**_unused`) | 1, 2, 3, 4, 5, 6, 7 |
| §3 "Config impact" (flip to new providers) | 12 |
| §3 "`scripts/backtest_fetch.py` cleanup" | 13 |
| §4 per-provider tests | 1, 2, 3, 4, 5, 6, 7, 9, 10, 11 |
| §4 registry-level swap test | 14 |
| §4 backfill integration test | 15 |
| §5 commit ordering | 1–7 (combined first commit split into 7 narrow commits for SAD-friendly review); 9, 10, 11, 12, 13 follow spec order |

**Placeholder scan:** No TBDs, no "implement later", no "similar to Task N", no "appropriate error handling". Every step contains the actual code or command.

**Type consistency:** `**_unused`, `as_of: datetime`, `_fetch_xbrl_facts`, `_fetch_price_series`, `_fill_quarterly_ratios` — names match across the tasks that reference them.

**Out-of-scope items** (per spec §1): social_sentiment historical, cloud parallel execution, live deployment work. No task in this plan touches those.
