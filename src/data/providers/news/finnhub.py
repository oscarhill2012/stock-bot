"""Finnhub news provider — ``company_news`` endpoint (rate-limited via registry).

**Backtest context**
Finnhub is a candidate news source for backtest cache-fill when Alpha Vantage
is exhausted or IP-blocked.  Empirical probes (2026-05-18) confirmed:

- The free tier exposes the ``/company-news`` endpoint with ~1 year of
  retention from "today" — older windows (e.g. SVB March 2023 from a 2026
  vantage) return zero articles, not an error.
- Each ``/company-news`` call appears to silently truncate at ~250 articles
  for high-volume tickers, regardless of the requested span.  A 40-day
  request that should return 1000+ articles instead returns ~250 covering
  only the first 5 days, silently losing the rest.

**Weekly chunking + truncation detection**
To avoid the silent truncation, the resolved ``[from_date, to_date]`` window
is split into ≤ 7-day slices.  Each slice incurs one Finnhub API call.  When
any individual chunk returns ≥ ``_TRUNCATION_WARN_THRESHOLD`` articles a
warning is logged — the chunk may itself have been truncated and a finer
split is required for that ticker.  Articles from all chunks are merged and
de-duplicated by URL.

**Sentiment loss**
Finnhub's ``/company-news`` endpoint does not return per-article sentiment
scores — those live behind the paid ``/news-sentiment`` endpoint.  Articles
returned here therefore have ``NewsArticle.sentiment = None``.  Downstream
extractors (see ``src/contract/extractors/news.py``) default missing
sentiment to ``0.0``, which means switching the news provider from AV to
Finnhub loses the sentiment signal for the analyst.  Accept this trade-off
in exchange for unblocking the backtest fill; revisit once a paid tier is
viable or AV access is restored.

**PIT correctness**
``to_date`` is unconditionally clipped to ``as_of.date()`` so no article
published after the simulation clock can ever leak into a backtest tick,
regardless of how lazy the caller's window arithmetic is.

In addition to that **request-side** clip, the provider applies a
**response-side** PIT filter: every chunk's response is post-filtered to
drop articles whose ``datetime`` field is past ``window_end``.  This is
necessary because Finnhub's ``/company-news`` endpoint does **not**
strictly honour the ``to=`` parameter — diagnosed 2026-05-19 against the
``baseline-2025-09`` window, where queries with ``to=2025-10-13``
returned market-summary articles dated up to ``2026-03-30`` (some five
months past the upper bound).  The provider is the last line of defence
here: without the response-side filter, those rows reach the cache
table and corrupt every backtest tick within the window.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import finnhub

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import NewsArticle

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Maximum window size per individual Finnhub call.  Empirical probes show
# ``/company-news`` silently truncates at ~250 articles per call regardless
# of the requested span, so for high-volume tickers (AAPL, MSFT) anything
# over ~7 days risks lost coverage.  Seven days is a conservative default
# that costs at most ``ceil(window/7)`` extra API calls per ticker.
_MAX_CHUNK_DAYS = 7

# Warn when any single chunk returns this many articles or more — the chunk
# may have been truncated.  Set just below the empirical ~250 cap so a
# borderline response still surfaces in the logs.
_TRUNCATION_WARN_THRESHOLD = 240

# Default cap on articles returned from the merged, de-duplicated, sorted
# result.  ``None`` keeps everything.  Bumped from 50 to 200 so the
# backtest-cache fill preserves more context per (ticker, window).  The
# public dispatcher (``data.get_stock_news``) imposes its own default cap
# so live-pipeline callers are unaffected.
_DEFAULT_RETURN_LIMIT: int | None = 200


def _client() -> finnhub.Client:
    """Construct a Finnhub client keyed on the env-loaded API token.

    Built fresh on each call so tests can monkey-patch ``require_key``
    without poking module state, and so a rotated key takes effect on the
    next call without a process restart.

    Returns
    -------
    finnhub.Client
        Configured client ready for the ``company_news`` endpoint.
    """
    return finnhub.Client(api_key=require_key("FINNHUB_API_KEY"))


@with_retry
def _fetch_company_news(symbol: str, from_iso: str, to_iso: str) -> list[dict]:
    """Sync Finnhub ``/company-news`` call, wrapped in the project retry policy.

    Kept synchronous because the ``finnhub`` SDK does not expose an async
    interface; the async provider routes through ``asyncio.to_thread`` so
    the event loop stays responsive.

    Parameters
    ----------
    symbol:
        Upper-cased ticker symbol.
    from_iso:
        Inclusive lower-bound date in ``YYYY-MM-DD`` format.
    to_iso:
        Inclusive upper-bound date in ``YYYY-MM-DD`` format.

    Returns
    -------
    list[dict]
        Raw Finnhub response items, or ``[]`` if the API returns ``None``.
    """
    return _client().company_news(symbol, _from=from_iso, to=to_iso) or []


def _coerce_date(value: Any) -> date | None:
    """Return ``value`` as a ``date``, accepting ``date``, ``datetime``, or ``None``.

    Mirrors ``data.providers.news.alpha_vantage._coerce_date``.  Callers of
    the news dispatcher may hand the provider either a ``date`` (backtest
    fetcher) or a ``datetime`` (live pipeline tick), so coerce once here
    rather than scatter ``isinstance`` checks through the call body.

    Parameters
    ----------
    value:
        A ``date``, ``datetime``, or ``None``.

    Returns
    -------
    date | None
        ``value.date()`` for a ``datetime``, ``value`` for a ``date``,
        ``None`` if ``value`` is ``None`` or of any other type.
    """
    # datetime is a subclass of date, so check it first.
    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    return None


def _item_date(item: dict) -> date | None:
    """Return the publication ``date`` for a Finnhub article, or ``None``.

    Finnhub's ``/company-news`` payload uses Unix-epoch seconds for the
    ``datetime`` field.  Mirrors the parse done by ``_map_article`` but
    returns just the ``date`` component for window-membership tests —
    the response-side PIT filter only cares about which calendar day the
    article belongs to, not the precise instant.

    Parameters
    ----------
    item:
        One element from Finnhub's raw response list.

    Returns
    -------
    date | None
        The article's publication date in UTC, or ``None`` if the
        ``datetime`` field is missing, non-numeric, or non-positive —
        those rows are handed downstream so ``_map_article``'s
        ``MISSING_TIMESTAMP`` fallback can route them.
    """
    ts = item.get("datetime")

    if not isinstance(ts, (int, float)) or ts <= 0:
        return None

    return datetime.fromtimestamp(ts, tz=UTC).date()


def _chunk_window(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    """Split a ``[start, end]`` date window into ≤ ``chunk_days``-sized slices.

    The final chunk may be shorter than ``chunk_days``.  Slices are returned
    in chronological order (oldest first) so dedup-by-URL keeps the earliest
    occurrence of any boundary-straddling article.

    Parameters
    ----------
    start:
        Inclusive lower bound of the window.
    end:
        Inclusive upper bound of the window (already clipped to ``as_of``).
    chunk_days:
        Maximum number of days per slice (inclusive).

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


def _map_article(item: dict, symbol: str) -> NewsArticle:
    """Convert one raw Finnhub article dict into a ``NewsArticle``.

    Finnhub's ``/company-news`` payload uses Unix-epoch seconds for
    ``datetime`` and omits sentiment fields.  Missing or zero timestamps
    are routed through the project's ``MISSING_TIMESTAMP`` sentinel so the
    cache writer can decide whether to drop the row.

    Parameters
    ----------
    item:
        One element from Finnhub's response list.
    symbol:
        Upper-cased ticker symbol the request was made for.

    Returns
    -------
    NewsArticle
        Mapped article with ``sentiment=None`` (Finnhub free tier does not
        return per-article sentiment).
    """
    ts = item.get("datetime")

    if isinstance(ts, (int, float)) and ts > 0:
        published = datetime.fromtimestamp(ts, tz=UTC)
    else:
        # Import inside the function so the module load order is unaffected
        # by the missing-models shim.
        from data.models.missing import MISSING_TIMESTAMP
        published = MISSING_TIMESTAMP

    return NewsArticle(
        ticker=symbol,
        headline=item.get("headline", "") or "",
        summary=item.get("summary", "") or "",
        url=item.get("url", "") or "",
        source=item.get("source", "") or "",
        published_at=published,
        sentiment=None,
    )


@register(
    domain="news",
    name="finnhub",
    upstream="finnhub",
    # 50/min + burst=10 caps any 60-second window at 60 calls — exactly
    # Finnhub's free-tier limit — without leaving the bucket able to dump
    # a 30-call burst mid-window the way the original 60/30 config did.
    # Worst-case 429s observed 2026-05-19 when a 20-ticker fill against
    # /company-news with 7-day chunking issued ~160 Finnhub calls; the
    # old burst could empty half the window's budget before the bucket
    # noticed.  Shared with earnings/finnhub and social_sentiment/finnhub
    # via the registry's per-upstream limiter map.
    rate_per_minute=50,
    burst=10,
)
async def fetch(
    ticker: str,
    *,
    from_date: date | datetime,
    to_date: date | datetime,
    as_of: datetime,
    limit: int | None = _DEFAULT_RETURN_LIMIT,
    **_unused: Any,
) -> list[NewsArticle]:
    """Return news articles for ``ticker`` from Finnhub's ``/company-news`` endpoint.

    The resolved window is ``[from_date, min(to_date, as_of)]`` — the
    upper bound is unconditionally clipped to ``as_of`` for PIT safety.
    The window is then split into ≤ ``_MAX_CHUNK_DAYS``-day slices, one
    API call per chunk, with results de-duplicated by URL and sorted
    newest-first.

    Parameters
    ----------
    ticker:
        Stock symbol (e.g. ``"AAPL"``).  Case-insensitive — normalised to
        upper-case before the API call.
    from_date:
        Inclusive lower bound of the news window.  Accepts ``date`` or
        ``datetime``; coerced via ``_coerce_date``.
    to_date:
        Inclusive upper bound.  Accepts ``date`` or ``datetime``.  Always
        clipped at ``as_of`` regardless of caller intent.
    as_of:
        Simulation / backtest clock.  Hard upper bound on the query window
        even when ``to_date`` extends past it.
    limit:
        Maximum number of articles in the merged result.  ``None`` keeps
        every article in the resolved window.  Defaults to
        ``_DEFAULT_RETURN_LIMIT``.
    **_unused:
        Absorbs extra kwargs forwarded by ``dispatch`` (e.g.
        ``lookback_days``) that other news providers consume.

    Returns
    -------
    list[NewsArticle]
        Articles in the resolved window, de-duplicated by URL, sorted
        newest-first, and capped at ``limit``.  ``sentiment`` is always
        ``None`` — Finnhub's free tier does not expose per-article
        sentiment scores.

    Notes
    -----
    A warning is logged at ``WARNING`` level whenever a single chunk
    returns ≥ ``_TRUNCATION_WARN_THRESHOLD`` articles — that chunk may
    itself have been silently truncated by Finnhub and a finer split is
    needed for the affected ticker.
    """
    symbol = ticker.upper()

    # ── Resolve the query window ───────────────────────────────────────────
    # The dispatcher (``data.get_stock_news``) guarantees non-None values
    # for ``from_date``, ``to_date``, and ``as_of`` — but a tz-aware
    # ``datetime`` can arrive from live ticks while the backtest fetcher
    # passes plain ``date`` objects, so coerce uniformly.
    as_of_date   = _coerce_date(as_of) or as_of      # type: ignore[assignment]
    window_start = _coerce_date(from_date)
    explicit_end = _coerce_date(to_date)

    # Defensive: caller-supplied bounds must coerce cleanly.  Garbage in →
    # loud raise rather than silently empty results that downstream code
    # cannot distinguish from "no news".
    if window_start is None or explicit_end is None:
        raise ValueError(
            f"news.finnhub: could not coerce window bounds "
            f"from_date={from_date!r} to_date={to_date!r}"
        )

    # Upper bound: caller's ``to_date``, but never past ``as_of`` — this is
    # the provider's last-line-of-defence PIT cap.  The cap is unconditional
    # because a sloppy caller should not be able to leak future news into a
    # backtest tick.
    window_end = min(explicit_end, as_of_date)

    # A reversed window (``from_date > to_date`` after clipping) is a caller
    # bug.  The previous ``return []`` hid backtest mis-windowing for hours;
    # raise so the offending bounds surface immediately.
    if window_start > window_end:
        raise ValueError(
            f"news.finnhub: reversed news window for {symbol}: "
            f"window_start={window_start.isoformat()} > "
            f"window_end={window_end.isoformat()} "
            f"(from_date={from_date}, to_date={to_date}, as_of={as_of_date})"
        )

    chunks = _chunk_window(window_start, window_end, _MAX_CHUNK_DAYS)

    # ── Fetch every chunk, merging into a URL-keyed dict to de-duplicate ───
    # Articles that straddle a chunk boundary (e.g. an article tagged for
    # 23:59 on day N reappearing in the [N+1, N+7] chunk) would otherwise
    # show up twice.  Keyed by URL because Finnhub article IDs are not
    # stable across pulls.
    seen_urls: set[str] = set()
    all_articles: list[NewsArticle] = []

    for chunk_start, chunk_end in chunks:

        # ``_fetch_company_news`` is synchronous (Finnhub SDK has no async
        # client), so route every call through ``to_thread`` to keep the
        # event loop responsive.  The shared async rate limiter wired by
        # the ``@register`` decorator already throttles concurrent calls.
        raw = await asyncio.to_thread(
            _fetch_company_news,
            symbol,
            chunk_start.isoformat(),
            chunk_end.isoformat(),
        )

        # Truncation guard — log loudly so a fill operator notices that the
        # chunk size needs to drop for this particular ticker.  Does not
        # raise: the fill is best-effort and partial coverage beats a hard
        # abort across the whole watchlist.
        if len(raw) >= _TRUNCATION_WARN_THRESHOLD:
            logger.warning(
                "finnhub: chunk %s..%s for %s returned %d articles "
                "(>= truncation threshold %d) — coverage may be incomplete; "
                "consider lowering _MAX_CHUNK_DAYS for this ticker",
                chunk_start, chunk_end, symbol, len(raw),
                _TRUNCATION_WARN_THRESHOLD,
            )

        # ── Response-side PIT filter ───────────────────────────────────────
        # Finnhub's ``/company-news`` does not strictly honour ``to=`` — it
        # routinely returns articles dated past the requested upper bound
        # (sometimes months past for cross-ticker market-summary stories).
        # Drop every article whose parsed publication date is past
        # ``window_end`` so the cache cannot receive a future-dated row no
        # matter what the API hands back.  Articles whose ``datetime`` field
        # is missing or unparseable are kept here; ``_map_article``'s
        # ``MISSING_TIMESTAMP`` fallback handles them downstream.
        dropped_future = 0
        pit_filtered:   list[dict] = []

        for item in raw:
            item_date = _item_date(item)

            if item_date is not None and item_date > window_end:
                dropped_future += 1
                continue

            pit_filtered.append(item)

        if dropped_future > 0:
            logger.warning(
                "finnhub: chunk %s..%s for %s dropped %d/%d future-dated "
                "articles (published_at > window_end %s) — Finnhub does not "
                "strictly honour to=",
                chunk_start, chunk_end, symbol, dropped_future, len(raw),
                window_end,
            )

        for item in pit_filtered:
            url = item.get("url") or ""

            # Empty-URL articles cannot be de-duplicated by URL; skip them
            # to avoid the false-positive "all dedupe to one row" failure
            # mode when several articles share a blank URL.
            if not url or url in seen_urls:
                continue

            seen_urls.add(url)
            all_articles.append(_map_article(item, symbol))

    # ── Sort & cap ─────────────────────────────────────────────────────────
    # Sort newest-first across the merged result so callers asking for
    # ``limit`` get the most recent articles (analyst preference).  Articles
    # with ``MISSING_TIMESTAMP`` sort to the end via the sentinel year.
    all_articles.sort(key=lambda a: a.published_at, reverse=True)

    if limit is not None:
        all_articles = all_articles[:limit]

    return all_articles
