"""Behavioural contract: every registered provider for a domain returns
DOMAIN_SHAPES[domain].

Phase 7.6 lands this test with xfail markers on every domain whose live
and cache implementations are not yet aligned (per audit).  Each Phase B
task removes one xfail by aligning live and cache providers.

Live-only domains (``earnings``, ``analyst_consensus``, ``short_interest``,
``options``) skip the cache half entirely.

Mocking strategy
----------------
Each live branch patches the IO boundary of the real provider module so that
no network call is made.  Where the boundary is a yfinance ``Ticker``, we
monkeypatch ``yf.Ticker``; where it is ``httpx.AsyncClient``, we replace the
constructor with a lightweight async context-manager stub.  The goal is to
reach the return-value construction path in the provider, not to re-test its
logic (that is covered by the per-provider unit tests).

Cache branches seed an in-memory SQLite ``CachedDataStore``, install it via
``set_store``, then call the cache provider's ``fetch`` directly.

Domains in ``_PENDING_ALIGNMENT`` are marked ``xfail(strict=True)`` — the
test is expected to fail because the provider's return shape diverges from the
canonical ``DOMAIN_SHAPES`` entry.  Phase B tasks remove each entry as they
align the shapes.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from data.registry import DOMAIN_SHAPES, DomainShape


# ---------------------------------------------------------------------------
# Async HTTP stub helpers
# ---------------------------------------------------------------------------

class _AsyncCM:
    """Minimal async context-manager wrapping a MagicMock HTTP response.

    Used to replace ``httpx.AsyncClient`` with a stub that returns a fixed
    JSON payload from its ``get()`` and ``post()`` coroutines.

    Parameters
    ----------
    resp:
        Pre-built ``MagicMock`` whose ``.json()`` returns the desired payload.
    """

    def __init__(self, resp: MagicMock) -> None:
        self._resp = resp

    async def __aenter__(self) -> _AsyncCM:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def get(self, *_a: Any, **_k: Any) -> MagicMock:
        """Return the stub response for GET calls."""
        return self._resp

    async def post(self, *_a: Any, **_k: Any) -> MagicMock:
        """Return the stub response for POST calls."""
        return self._resp


def _make_resp(payload: Any) -> MagicMock:
    """Construct a MagicMock HTTP response whose ``.json()`` returns ``payload``.

    Parameters
    ----------
    payload:
        The value ``resp.json()`` should return (typically a dict or list).

    Returns
    -------
    MagicMock
        Configured mock; ``raise_for_status`` is a no-op.
    """
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = lambda: None
    return resp


# ---------------------------------------------------------------------------
# Shape-match helper
# ---------------------------------------------------------------------------

def _matches_shape(value: object, shape: DomainShape) -> bool:
    """Return True iff ``value`` structurally satisfies the canonical ``shape``.

    Parameters
    ----------
    value:
        The object returned by a provider's ``fetch`` call.
    shape:
        The canonical ``DomainShape`` for that domain from ``DOMAIN_SHAPES``.

    Returns
    -------
    bool
        ``True`` when ``value``'s type is consistent with the shape contract.

    Raises
    ------
    ValueError
        If ``shape.container`` is not one of the known literals.
    """
    if shape.container in ("single", "bundle"):
        return isinstance(value, shape.payload_type)

    if shape.container == "list":
        if not isinstance(value, list):
            return False
        return all(isinstance(item, shape.payload_type) for item in value)

    raise ValueError(f"unknown container: {shape.container!r}")


# ---------------------------------------------------------------------------
# Live-provider dispatch
# ---------------------------------------------------------------------------

async def _call_live_provider(domain: str, monkeypatch: pytest.MonkeyPatch) -> object:
    """Invoke the live provider for ``domain`` with its IO boundary mocked.

    Each branch patches the minimal surface needed so the provider executes
    its transformation logic and returns a real canonical-type value — or the
    drifted type, for domains in ``_PENDING_ALIGNMENT``.  No real network
    calls are made.

    Parameters
    ----------
    domain:
        One of the keys in ``DOMAIN_SHAPES``.
    monkeypatch:
        pytest monkeypatch fixture, used to patch module-level names.

    Returns
    -------
    object
        Whatever the live provider returns; the contract test asserts its type.

    Raises
    ------
    ValueError
        If ``domain`` has no live-provider stub defined here.
    """
    _as_of = datetime(2023, 3, 10, tzinfo=UTC)
    _as_of_date = _as_of.date()

    # ── price_history ─────────────────────────────────────────────────────────
    if domain == "price_history":
        # The stats/yfinance provider calls _yt_raw -> yf.Ticker().history().
        # We patch _yt_raw directly (it is an lru_cache'd function) with a
        # lambda that returns a minimal empty-DataFrame dict so the provider
        # returns a PriceHistory with no bars rather than touching the network.
        from data.providers.stats import yfinance as mod

        fake_raw = {
            "history": pd.DataFrame(),
            "info":    {},
            "fast":    {},
        }
        monkeypatch.setattr(mod, "_yt_raw", lambda *_a, **_k: fake_raw)
        return await mod.fetch_price_history(
            "AAPL", as_of=_as_of, period="1y", interval="1d",
        )

    # ── company_ratios ────────────────────────────────────────────────────────
    # Use the yfinance (non-PIT) provider: it is simpler to stub — just _yt_raw.
    # The pit_composite provider would need edgartools + yfinance mocked
    # concurrently; for the contract test the yfinance provider is sufficient.
    if domain == "company_ratios":
        from data.providers.stats import yfinance as mod

        fake_raw = {
            "history": pd.DataFrame(),
            "info":    {},
            "fast":    {},
        }
        monkeypatch.setattr(mod, "_yt_raw", lambda *_a, **_k: fake_raw)
        return await mod.fetch_company_ratios(
            "AAPL", as_of=_as_of, period="1y", interval="1d",
        )

    # ── news ──────────────────────────────────────────────────────────────────
    # Use the Finnhub news provider.  It calls _client().company_news via
    # asyncio.to_thread; patch the module-level _fetch_company_news function.
    if domain == "news":
        from data.providers.news import finnhub as mod

        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setattr(
            mod, "_fetch_company_news",
            lambda *_a, **_k: [
                {
                    "datetime":  int(_as_of.timestamp()),
                    "headline":  "Headline",
                    "summary":   "Summary",
                    "url":       "https://example.com/1",
                    "source":    "finnhub",
                    "sentiment": None,
                    "related":   "AAPL",
                },
            ],
        )
        return await mod.fetch(
            "AAPL",
            as_of=_as_of,
            from_date=_as_of_date - timedelta(days=7),
            to_date=_as_of_date,
        )

    # ── social_sentiment ──────────────────────────────────────────────────────
    # Finnhub social sentiment; patches _fetch_social so no finnhub SDK call.
    if domain == "social_sentiment":
        from data.providers.social_sentiment import finnhub as mod

        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setattr(
            mod, "_fetch_social",
            lambda _sym: {
                "reddit":  [{"mention": 5, "positiveScore": 0.6, "negativeScore": 0.2}],
                "twitter": [{"mention": 3, "positiveScore": 0.7, "negativeScore": 0.1}],
            },
        )
        return await mod.fetch("AAPL", as_of=_as_of)

    # ── insider_trades ────────────────────────────────────────────────────────
    # EDGAR provider's blocking _list_form4_filings returns a list of filing
    # objects; _fetch_and_parse_one maps one filing -> Form4Bundle slice.
    # Easiest to patch _list_form4_filings to return [] so the loop body is
    # skipped and we get back an empty-but-correct Form4Bundle immediately.
    if domain == "insider_trades":
        from data.providers.insider_trades import edgar as mod

        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setattr(
            mod, "_list_form4_filings", lambda *_a, **_k: [],
        )
        return await mod.fetch("AAPL", as_of=_as_of, lookback_days=30)

    # ── politician_trades ─────────────────────────────────────────────────────
    # Quiver provider uses requests; patch _fetch_trades.
    if domain == "politician_trades":
        from data.providers.politician_trades import quiver as mod

        monkeypatch.setattr(mod, "_fetch_trades", lambda *_a, **_k: [])
        # quiver.fetch checks for QUIVER_API_KEY via os.environ; give it a stub.
        import os
        monkeypatch.setattr(os, "environ", {**os.environ, "QUIVER_API_KEY": "stub"})
        return await mod.fetch("AAPL", as_of=_as_of, lookback_days=30)

    # ── notable_holders ───────────────────────────────────────────────────────
    # EDGAR provider; patch _iter_filings to return [] (no filing objects).
    if domain == "notable_holders":
        from data.providers.notable_holders import edgar as mod

        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setattr(
            mod, "_iter_filings", lambda *_a, **_k: [],
        )
        return await mod.fetch("AAPL", as_of=_as_of, lookback_days=180)

    # ── filings ───────────────────────────────────────────────────────────────
    # EDGAR provider; patch _iter_filings to return [].
    if domain == "filings":
        from data.providers.filings import edgar as mod

        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setattr(
            mod, "_iter_filings", lambda *_a, **_k: [],
        )
        return await mod.fetch("AAPL", as_of=_as_of)

    # ── earnings ──────────────────────────────────────────────────────────────
    # Finnhub earnings; patch httpx.AsyncClient.
    if domain == "earnings":
        from data.providers.earnings import finnhub as mod

        payload = {
            "earningsCalendar": [
                {
                    "symbol":          "AAPL",
                    "date":            "2023-02-02",
                    "epsActual":       1.88,
                    "epsEstimate":     1.94,
                    "revenueActual":   1.17e11,
                    "revenueEstimate": 1.21e11,
                    "quarter":         1,
                    "year":            2023,
                },
            ],
        }
        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setattr(
            mod.httpx, "AsyncClient",
            lambda *_a, **_k: _AsyncCM(_make_resp(payload)),
        )
        return await mod.fetch("AAPL", as_of=_as_of_date, lookback_quarters=4)

    # ── analyst_consensus ─────────────────────────────────────────────────────
    # yfinance provider; returns (AnalystRating, list[AnalystRevision]) — a
    # plain tuple, NOT AnalystConsensusBundle.  This is the known live drift.
    # The xfail mark on this domain ensures the failing assertion is expected.
    if domain == "analyst_consensus":
        from data.providers.analyst_consensus import yfinance as mod

        mock_ticker = MagicMock()
        mock_ticker.analyst_price_targets = {}
        mock_ticker.upgrades_downgrades   = pd.DataFrame()
        mock_ticker.recommendations_summary = pd.DataFrame()
        mock_ticker.info = {}
        monkeypatch.setattr(mod.yf, "Ticker", lambda _sym: mock_ticker)
        return await mod.fetch("AAPL", as_of=_as_of_date)

    # ── short_interest ────────────────────────────────────────────────────────
    # FINRA provider; patch the token cache + AsyncClient.
    if domain == "short_interest":
        from data.providers.short_interest import finra as mod

        token_resp = _make_resp({"access_token": "tok", "expires_in": 43200})
        data_resp  = _make_resp([
            {
                "securitiesInformationProcessorSymbolIdentifier": "AAPL",
                "tradeReportDate":      "2023-03-08",
                "marketCode":           "B",
                "shortParQuantity":     10,
                "shortExemptParQuantity": 0,
                "totalParQuantity":     100,
                "reportingFacilityCode": "NCTRF",
            },
        ])

        cm_calls = iter([_AsyncCM(token_resp), _AsyncCM(data_resp)])
        monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *_a, **_k: next(cm_calls))
        monkeypatch.setattr(mod, "require_key", lambda _k: "stub")
        monkeypatch.setitem(mod._token_cache, "token", None)
        monkeypatch.setitem(mod._token_cache, "expires_at", 0.0)
        return await mod.fetch("AAPL", as_of=_as_of_date, lookback_days=30)

    # ── options ───────────────────────────────────────────────────────────────
    # Shell provider returns ``{}``; no mocking needed — it never touches the
    # network.  The xfail mark catches the dict ≠ list[OptionContract] mismatch.
    if domain == "options":
        from data.providers.options import yfinance as mod

        return await mod.fetch("AAPL", as_of=_as_of_date)

    raise ValueError(f"no live-provider stub defined for domain: {domain!r}")


# ---------------------------------------------------------------------------
# Cache-provider dispatch
# ---------------------------------------------------------------------------

async def _call_cache_provider(domain: str, store_path: Path) -> object:
    """Invoke the cache provider for ``domain`` against a seeded in-memory store.

    Creates a ``CachedDataStore`` at ``store_path``, seeds one minimal row for
    ``domain``, wires the store singleton via ``set_store``, imports the cache
    provider module (triggering its ``@register`` call), then calls its
    ``fetch`` directly.

    Parameters
    ----------
    domain:
        One of the cache-having domains (not in ``_LIVE_ONLY``).
    store_path:
        Path for the SQLite file (a ``tmp_path``-scoped directory).

    Returns
    -------
    object
        Whatever the cache provider returns; the contract test asserts its type.

    Raises
    ------
    ValueError
        If ``domain`` is not recognised.
    """
    from backtest.cache.store import CachedDataStore
    from backtest.providers import _store_handle
    from data.models import (
        CompanyRatios,
        Filing,
        Form4Bundle,
        InsiderTrade,
        NewsArticle,
        NotableHolder,
        OHLCBar,
        PoliticianTrade,
        PriceHistory,
        SocialSentiment,
    )

    store   = CachedDataStore(store_path / f"{domain}.sqlite")
    _store_handle.set_store(store)

    _as_of  = datetime(2023, 3, 15, tzinfo=UTC)

    try:
        # ── price_history ─────────────────────────────────────────────────────
        if domain == "price_history":
            from backtest.providers import price_history_cache as mod  # noqa: PLC0415

            store.write_ohlcv("AAPL", [
                OHLCBar(
                    timestamp=datetime(2023, 3, 10, tzinfo=UTC),
                    open=150.0, high=155.0, low=149.0, close=153.0, volume=1_000_000,
                ),
            ])
            return await mod.fetch("AAPL", as_of=_as_of, period="1y", phase="close")

        # ── company_ratios ────────────────────────────────────────────────────
        # Cache provider returns ``CompanyRatios | None`` — the drift manifests
        # when no snapshot exists before ``as_of`` (the provider returns None
        # instead of raising or returning a sentinel CompanyRatios).  We
        # intentionally do NOT seed any row so the ``None`` path is exercised,
        # exposing the shape divergence the xfail is meant to catch.
        if domain == "company_ratios":
            from backtest.providers import company_ratios_cache as mod  # noqa: PLC0415

            # No write_company_ratios call — empty store means the provider
            # returns None, which violates the single/CompanyRatios contract.
            return await mod.fetch("AAPL", as_of=_as_of)

        # ── news ──────────────────────────────────────────────────────────────
        if domain == "news":
            from backtest.providers import news_cache as mod  # noqa: PLC0415

            store.write_news("AAPL", [
                NewsArticle(
                    ticker="AAPL",
                    url="https://example.com/1",
                    headline="Test headline",
                    summary="Test summary",
                    source="finnhub",
                    published_at=datetime(2023, 3, 10, tzinfo=UTC),
                ),
            ])
            return await mod.fetch("AAPL", as_of=_as_of, lookback_days=30)

        # ── social_sentiment ──────────────────────────────────────────────────
        # v1 stub always returns None — known drift; xfail catches it.
        if domain == "social_sentiment":
            from backtest.providers import social_sentiment_cache as mod  # noqa: PLC0415

            # No seeding required: the v1 stub ignores the store entirely.
            return await mod.fetch("AAPL", as_of=_as_of)

        # ── insider_trades ────────────────────────────────────────────────────
        if domain == "insider_trades":
            from backtest.providers import insider_trades_cache as mod  # noqa: PLC0415

            store.write_insider_trades("AAPL", [
                InsiderTrade(
                    ticker="AAPL",
                    insider_name="John Doe",
                    insider_title="CEO",
                    side="buy",
                    shares=1_000,
                    price_per_share=150.0,
                    transaction_date=date(2023, 3, 8),
                    filed_at=datetime(2023, 3, 9, tzinfo=UTC),
                    form_type="4",
                ),
            ])
            return await mod.fetch("AAPL", as_of=_as_of, lookback_days=30)

        # ── politician_trades ─────────────────────────────────────────────────
        if domain == "politician_trades":
            from backtest.providers import politician_trades_cache as mod  # noqa: PLC0415

            store.write_politician_trades("AAPL", [
                PoliticianTrade(
                    ticker="AAPL",
                    politician="Nancy Pelosi",
                    chamber="house",
                    party="D",
                    side="buy",
                    transaction_date=date(2023, 3, 8),
                    disclosure_date=date(2023, 3, 9),
                    amount_min_usd=15_000.0,
                    amount_max_usd=50_000.0,
                ),
            ])
            return await mod.fetch("AAPL", as_of=_as_of, lookback_days=30)

        # ── notable_holders ───────────────────────────────────────────────────
        if domain == "notable_holders":
            from backtest.providers import notable_holders_cache as mod  # noqa: PLC0415

            store.write_notable_holders("AAPL", [
                NotableHolder(
                    ticker="AAPL",
                    holder="Berkshire Hathaway",
                    form_type="SC 13G",
                    intent="passive",
                    is_amendment=False,
                    filed_at=datetime(2023, 3, 9, tzinfo=UTC),
                    accession_no="0000012345-23-000001",
                    url="https://sec.gov/1",
                    percent_of_class=5.5,
                ),
            ])
            return await mod.fetch("AAPL", as_of=_as_of, lookback_days=365)

        # ── filings ───────────────────────────────────────────────────────────
        if domain == "filings":
            from backtest.providers import filings_cache as mod  # noqa: PLC0415

            store.write_filings("AAPL", [
                Filing(
                    ticker="AAPL",
                    form_type="10-K",
                    filed_at=datetime(2023, 3, 9, tzinfo=UTC),
                    accession_no="0000012345-23-000002",
                    url="https://sec.gov/2",
                    title="Annual Report",
                ),
            ])
            return await mod.fetch("AAPL", as_of=_as_of, lookback_days=365)

    finally:
        # Always clear the singleton so subsequent tests get a fresh store.
        _store_handle.clear_store()

    raise ValueError(f"no cache-provider stub defined for domain: {domain!r}")


# ---------------------------------------------------------------------------
# Parametrisation
# ---------------------------------------------------------------------------

# Domains with no cache provider registered today.
# Cache test is skipped for these; only the live half runs.
_LIVE_ONLY: set[str] = {"earnings", "analyst_consensus", "short_interest", "options"}

# Domains whose **live** provider return type diverges from the canonical
# DOMAIN_SHAPES entry.  Source: audit column "Drift fix needed = live".
# Each Phase B alignment task removes the relevant entry.
_LIVE_PENDING: set[str] = {
    "analyst_consensus",    # returns tuple[AnalystRating, list[AnalystRevision]]; needs AnalystConsensusBundle wrapper
    "options",              # shell returns dict; OptionContract model not yet defined
}

# Domains whose **cache** provider return type diverges from the canonical
# DOMAIN_SHAPES entry.  Source: audit column "Drift fix needed = cache".
_CACHE_PENDING: set[str] = {
    "company_ratios",   # returns CompanyRatios | None; canonical is single/CompanyRatios
    "social_sentiment", # v1 stub returns None; canonical is single/SocialSentiment
}


def _live_params() -> list:
    """Build parametrisation entries for the live-provider contract test.

    Domains in ``_LIVE_PENDING`` get ``xfail(strict=True)`` because their live
    provider's return type does not yet match the canonical shape.  Domains in
    ``_CACHE_PENDING`` (cache-side drift only) are plain ``passed`` entries
    because the live provider is already aligned.

    Returns
    -------
    list
        Ordered list of ``pytest.param`` entries, one per domain.
    """
    entries = []
    for domain in sorted(DOMAIN_SHAPES.keys()):
        if domain in _LIVE_PENDING:
            entries.append(
                pytest.param(
                    domain,
                    marks=pytest.mark.xfail(
                        strict=True,
                        reason=f"{domain} live shape drift — see Phase B task",
                    ),
                )
            )
        else:
            entries.append(pytest.param(domain))
    return entries


def _cache_params() -> list:
    """Build parametrisation entries for the cache-provider contract test.

    Domains in ``_CACHE_PENDING`` get ``xfail(strict=True)`` because their
    cache provider's return type does not yet match the canonical shape.
    Domains in ``_LIVE_PENDING`` are plain entries here because their cache
    half is either ``skipped`` (live-only) or already aligned.

    Returns
    -------
    list
        Ordered list of ``pytest.param`` entries, one per domain.
    """
    entries = []
    for domain in sorted(DOMAIN_SHAPES.keys()):
        if domain in _CACHE_PENDING:
            entries.append(
                pytest.param(
                    domain,
                    marks=pytest.mark.xfail(
                        strict=True,
                        reason=f"{domain} cache shape drift — see Phase B task",
                    ),
                )
            )
        else:
            entries.append(pytest.param(domain))
    return entries


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

@pytest.mark.contract
@pytest.mark.parametrize("domain", _live_params())
async def test_live_provider_returns_canonical_shape(
    domain: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live provider for ``domain`` must return ``DOMAIN_SHAPES[domain]``.

    Parameters
    ----------
    domain:
        One of the registered domain names from ``DOMAIN_SHAPES``.
    monkeypatch:
        Provided by pytest; used to patch IO boundaries in each branch.
    """
    shape  = DOMAIN_SHAPES[domain]
    result = await _call_live_provider(domain, monkeypatch)
    assert _matches_shape(result, shape), (
        f"live provider for {domain!r} returned {type(result).__name__}, "
        f"expected container={shape.container!r} payload={shape.payload_type.__name__!r}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("domain", _cache_params())
async def test_cache_provider_returns_canonical_shape(
    domain: str,
    tmp_path: Path,
) -> None:
    """Cache provider for ``domain`` must return ``DOMAIN_SHAPES[domain]``.

    Skipped for live-only domains (no cache provider exists today).

    Parameters
    ----------
    domain:
        One of the registered domain names from ``DOMAIN_SHAPES``.
    tmp_path:
        pytest-provided temporary directory; each test gets its own SQLite file.
    """
    if domain in _LIVE_ONLY:
        pytest.skip(f"{domain} has no cache provider — live-only")

    shape  = DOMAIN_SHAPES[domain]
    result = await _call_cache_provider(domain, tmp_path)
    assert _matches_shape(result, shape), (
        f"cache provider for {domain!r} returned {type(result).__name__}, "
        f"expected container={shape.container!r} payload={shape.payload_type.__name__!r}"
    )
