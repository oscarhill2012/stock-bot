"""Alpha Vantage ``NEWS_SENTIMENT`` provider — backtest cache-fill only.

**Backtest context**
This provider is the designated *backtest cache-fill* source for news data.
Alpha Vantage has a verified historical archive going back to at least 2023
(confirmed in Phase 0 preflight notes A6 — 21 / 25 / 9 articles per week
across Jan / Jun / Dec 2023 SVB-era windows, no rate-limit messaging).

**Free-tier budget**
The AV free tier allows 25 requests/day.  A 50-ticker watchlist therefore
requires ≈ 2 calendar days of staggered fill per ticker-day of history (run
overnight in batches of ~25 tickers/day).  This is acceptable for a one-shot
fill operation; once cached, backtest replay makes no further AV calls.

**Monthly chunking**
When ``lookback_days`` exceeds 30, the ``[as_of - lookback_days, as_of]``
window is split into ≤ 30-day slices.  Each slice results in one AV API call;
results are merged and de-duplicated by URL before being returned.  This keeps
individual AV responses within manageable size limits and prevents silent
truncation when AV's ``limit`` cap (1 000 articles) would otherwise clip a
long window.

**Multi-ticker batching — deferred**
The Provider protocol is single-ticker (``fetch(ticker, *, as_of, ...)``), so
multi-ticker batching cannot be exposed through the current signature without a
new batch entry point.  AV's ``tickers=`` param already accepts a
comma-separated list, so batching is technically feasible — but the cache-fill
driver would need to accumulate tickers before issuing calls, which is a
non-trivial scheduler change.  This is deferred until the cache-fill driver
supports batch entry points.

**Live provider**
The live runtime news provider is NOT selected in v1.  Per the project's
"provider switching must be one config flip" rule, swapping to a paid-tier
provider (e.g. AV Premium, Finnhub, Polygon.io) is a single edit in
``config/data.json`` — ``news: "alpha_vantage"`` → ``news: "<paid-provider>"``.
No code change is needed.

**Fields populated**
- ``NewsArticle.sentiment``  ← ``overall_sentiment_score``  (per-article, [-1, 1])
- ``NewsArticle.relevance``  ← ``ticker_sentiment[].relevance_score`` for the
  requested ticker ([0, 1]); ``None`` if the ticker is absent from the list.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from data.models.news import NewsArticle
from data.registry import register
from data.secrets import require_key

# Alpha Vantage REST base URL — all queries go through the single /query endpoint.
_BASE = "https://www.alphavantage.co/query"

# Top-level keys AV uses to communicate non-data states inside a HTTP 200
# envelope.  The free tier returns a `"Information"` payload (with HTTP 200
# and *no* `feed` key) once the 25-request/day budget is exhausted — which is
# indistinguishable from a genuinely empty feed unless we look for these
# markers explicitly.  Audit 2026-05-18 traced an entire SVB cache fill with
# `news` showing `status=ok, rows_written=0` to exactly this blind spot.
_ENVELOPE_KEYS: tuple[str, ...] = ("Information", "Note", "Error Message")


class AlphaVantageEnvelopeError(RuntimeError):
    """Raised when AV returns a non-data envelope (rate-limit, quota, error).

    Distinguished from a genuinely empty feed so callers (cache_runs, in
    particular) can record the failure instead of silently writing zero
    rows.  The HTTP layer cannot detect this — AV returns HTTP 200 with
    `{"Information": "...25 requests/day..."}` and no `feed` key.
    """

# Hard limit on articles returned per request (AV maximum is 1000; 200 is a
# safe default that keeps response sizes manageable without losing coverage).
_DEFAULT_ARTICLE_LIMIT = 200

# Maximum window size per individual AV call.  AV can return up to 1 000
# articles per request, but multi-month windows risk silent truncation.
# Splitting into ≤ 30-day slices keeps each call focused and the response
# sizes predictable.
_MAX_CHUNK_DAYS = 30


def _coerce_date(value: Any) -> date | None:
    """Return ``value`` as a ``date``, accepting ``date``, ``datetime``, or ``None``.

    The dispatcher (``data.get_stock_news``) hands the news provider
    ``from_date`` / ``to_date`` values that may be either ``date`` or
    ``datetime``, depending on caller — the backtest fetcher passes ``date``
    via window config, while the live pipeline could plausibly pass a tz-aware
    ``datetime``.  Normalising once here avoids a chain of ``isinstance``
    checks inside the call body.

    Parameters
    ----------
    value:
        A ``date``, ``datetime``, or ``None``.

    Returns
    -------
    date | None
        ``value.date()`` for a ``datetime``, ``value`` for a ``date``,
        ``None`` if ``value`` is ``None`` or any other type (callers fall
        back to ``lookback_days`` when this returns ``None``).
    """
    # datetime is a subclass of date, so check it first.
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return None


def _parse_ts(s: str) -> datetime:
    """Parse an Alpha Vantage timestamp string into a UTC-aware datetime.

    Alpha Vantage uses ``"YYYYMMDDTHHMMSS"`` (UTC, no zone suffix).

    Parameters
    ----------
    s:
        Raw timestamp string from the AV ``feed[]`` array.

    Returns
    -------
    datetime
        UTC-aware datetime object.
    """
    return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)


def _chunk_window(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """Split a ``[start, end]`` date window into ≤ ``chunk_days``-sized slices.

    The final chunk may be shorter than ``chunk_days``.  Slices are returned
    in chronological order (oldest first).

    Parameters
    ----------
    start:
        Inclusive window start date.
    end:
        Inclusive window end date (i.e. ``as_of``).
    chunk_days:
        Maximum number of days per slice.

    Returns
    -------
    list[tuple[date, date]]
        List of ``(chunk_start, chunk_end)`` pairs covering the full window.
    """
    chunks: list[tuple[date, date]] = []
    chunk_start = start

    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), end)
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)

    return chunks


def _extract_articles(
    payload: dict,
    symbol: str,
) -> list[NewsArticle]:
    """Convert a single AV ``NEWS_SENTIMENT`` response payload into ``NewsArticle`` objects.

    Extracts ``sentiment`` from ``overall_sentiment_score`` (article-level) and
    ``relevance`` from the matching entry in ``ticker_sentiment[]``.

    Parameters
    ----------
    payload:
        Parsed JSON response dict from a single AV API call.
    symbol:
        Upper-cased ticker symbol used to locate the per-ticker relevance entry.

    Returns
    -------
    list[NewsArticle]
        Mapped articles; empty list if ``feed`` is absent or empty.
    """
    articles: list[NewsArticle] = []

    for row in payload.get("feed") or []:

        # ── Per-ticker relevance ─────────────────────────────────────────────
        # AV returns a ``ticker_sentiment`` list with one entry per symbol
        # mentioned in the article.  Extract the relevance score for the
        # requested ticker; default to None if the ticker is absent.
        relevance: float | None = None

        for ts in row.get("ticker_sentiment") or []:
            if ts.get("ticker") == symbol:
                try:
                    relevance = float(ts["relevance_score"])
                except (KeyError, TypeError, ValueError):
                    relevance = None
                break

        # ── Sentiment ────────────────────────────────────────────────────────
        # ``overall_sentiment_score`` is a float in [-1, 1] at article level.
        # It may be absent for older feed items — treat missing as None.
        sentiment: float | None = None
        raw_sentiment = row.get("overall_sentiment_score")

        if raw_sentiment is not None:
            try:
                sentiment = float(raw_sentiment)
            except (TypeError, ValueError):
                sentiment = None

        articles.append(NewsArticle(
            ticker=symbol,
            headline=row.get("title") or "",
            summary=row.get("summary") or "",
            url=row.get("url") or "",
            source=row.get("source") or "alpha_vantage",
            published_at=_parse_ts(row["time_published"]),
            sentiment=sentiment,
            relevance=relevance,
        ))

    return articles


@register(
    domain="news",
    name="alpha_vantage",
    upstream="alpha_vantage",
    rate_per_minute=5,
    burst=2,
    # AV free tier is 25 req/day (≈ 1 req/min averaged over a working day).
    # Conservative 5/min gives headroom for brief bursts while keeping the
    # daily budget intact when multiple provider domains share the upstream.
)
async def fetch(
    ticker: str,
    *,
    as_of: date,
    lookback_days: int = 7,
    from_date: date | datetime | None = None,
    to_date: date | datetime | None = None,
    **_: Any,
) -> list[NewsArticle]:
    """Return news articles for ``ticker`` from Alpha Vantage ``NEWS_SENTIMENT``.

    Window resolution (most-specific-wins):

    - If ``from_date`` is supplied, ``window_start = from_date`` — the caller
      has explicitly requested a span, so ignore ``lookback_days``.
    - Otherwise ``window_start = as_of - lookback_days`` (legacy behaviour).
    - ``window_end = min(to_date, as_of)`` if ``to_date`` is supplied,
      otherwise ``window_end = as_of``.  The ``as_of`` cap is non-negotiable
      and exists to preserve point-in-time correctness — no article published
      after the simulation clock can ever be returned, even if a caller asks
      for one.

    Splits the resolved window into ≤ 30-day chunks to avoid AV response
    truncation and keep individual request sizes manageable.  Results from
    all chunks are merged and de-duplicated by URL before being returned.

    Parameters
    ----------
    ticker:
        Stock symbol (e.g. ``"AAPL"``).  Case-insensitive — normalised to
        upper-case before the API call.
    as_of:
        The simulation / backtest date.  Hard upper bound on the query window
        even when ``to_date`` extends past it.
    lookback_days:
        Days to look back from ``as_of`` when ``from_date`` is not supplied.
        Defaults to 7.  Windows longer than 30 days are automatically split
        into monthly chunks; each chunk incurs one AV API request.
    from_date:
        Explicit lower bound of the news window.  Accepts ``date`` or
        ``datetime``.  When present, overrides ``lookback_days``.  This is the
        kwarg ``data.get_stock_news`` forwards — without honouring it, the
        provider silently underfetches whenever the dispatcher asks for a
        custom span (e.g. a backtest fill covering more than 7 days).
    to_date:
        Explicit upper bound of the news window.  Accepts ``date`` or
        ``datetime``.  Clipped to ``as_of`` for PIT safety.
    **_:
        Absorbs other extra keyword arguments forwarded by ``dispatch`` so
        callers do not need to filter kwargs.

    Returns
    -------
    list[NewsArticle]
        Articles in the resolved window, de-duplicated by URL and ordered
        chunk-by-chunk (oldest chunk first, newest-first within each chunk as
        returned by AV).  Returns an empty list when all feeds are empty.

    Raises
    ------
    data.secrets.SecretMissingError
        If ``ALPHA_VANTAGE_API_KEY`` is not set in the environment.
    httpx.HTTPStatusError
        On a non-2xx HTTP response from Alpha Vantage.
    AlphaVantageEnvelopeError
        If AV responds with a non-data envelope (``Information``, ``Note``,
        or ``Error Message`` key — typically a rate-limit or quota
        notification served at HTTP 200).
    """
    symbol = ticker.upper()

    # Retrieve the key at call time so the module can be imported without a
    # configured .env (import-time failures would break the registry).
    api_key = require_key("ALPHA_VANTAGE_API_KEY")

    # ── Resolve the query window ───────────────────────────────────────────
    # `as_of` may arrive as either a date or a datetime depending on caller
    # (live pipeline vs. backtest fetcher).  Normalise to date before any
    # arithmetic so chunk boundaries are consistent.
    as_of_date = _coerce_date(as_of) or as_of  # leave as-is if coerce fails

    explicit_start = _coerce_date(from_date)
    explicit_end   = _coerce_date(to_date)

    # Lower bound: explicit `from_date` wins; otherwise fall back to the
    # legacy `as_of - lookback_days` calculation.
    window_start = (
        explicit_start
        if explicit_start is not None
        else as_of_date - timedelta(days=lookback_days)
    )

    # Upper bound: explicit `to_date` if given, but never past `as_of`.
    # Capping here is the only thing standing between a sloppy caller and a
    # PIT leak, so the cap is unconditional.
    window_end = min(explicit_end, as_of_date) if explicit_end is not None else as_of_date

    # A reversed window (caller passed a reversed range, or `from_date` is
    # after `as_of`) is a caller bug.  The previous ``return []`` masked
    # backtest mis-windowing; raise so the offending bounds surface.
    if window_start > window_end:
        raise ValueError(
            f"news.alpha_vantage: reversed news window for {symbol}: "
            f"window_start={window_start.isoformat()} > "
            f"window_end={window_end.isoformat()} "
            f"(from_date={from_date}, to_date={to_date}, as_of={as_of_date})"
        )

    chunks = _chunk_window(window_start, window_end, _MAX_CHUNK_DAYS)

    # Track seen URLs to de-duplicate articles that straddle chunk boundaries.
    seen_urls: set[str] = set()
    all_articles: list[NewsArticle] = []

    # Identify the client to AV so a bare ``python-httpx/X`` User-Agent does
    # not look bot-like to upstream throttlers.  Does not affect quota counting
    # against the free-tier 25/day budget — limits there are enforced on the
    # source IP regardless of headers — but is cheap insurance against
    # heuristic blocks elsewhere in the pipeline.
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(15.0),
        headers={"User-Agent": "StockBot-Backtest/1.0"},
    ) as client:

        for chunk_start, chunk_end in chunks:
            params = {
                "function":  "NEWS_SENTIMENT",
                "tickers":   symbol,
                # AV timestamp format: YYYYMMDDTHHmmss (24-h, no separators).
                "time_from": chunk_start.strftime("%Y%m%dT000000"),
                "time_to":   chunk_end.strftime("%Y%m%dT235959"),
                "limit":     _DEFAULT_ARTICLE_LIMIT,
                "apikey":    api_key,
            }

            resp = await client.get(_BASE, params=params)
            resp.raise_for_status()
            payload: dict = resp.json() or {}

            # Detect non-data envelopes BEFORE walking the feed.  AV serves
            # rate-limit/quota notices at HTTP 200 with no ``feed`` key, so
            # ``_extract_articles`` would silently return ``[]`` and the cache
            # writer would record status=ok / rows_written=0 — masking the
            # outage.  Raising lets the cache_runs layer surface it as an
            # error and prevents downstream tickers from being skipped under
            # the same exhausted quota.
            for envelope_key in _ENVELOPE_KEYS:
                msg = payload.get(envelope_key)

                if msg:
                    raise AlphaVantageEnvelopeError(
                        f"Alpha Vantage returned a {envelope_key!r} envelope "
                        f"(likely rate-limit or quota): {str(msg)[:200]}"
                    )

            for article in _extract_articles(payload, symbol):
                if article.url not in seen_urls:
                    seen_urls.add(article.url)
                    all_articles.append(article)

    return all_articles
