"""Specificity router — splits pooled watchlist news into company and macro streams.

Phase 14 Plan 2.  The roundup-demotion logic introduced in ``news/fetch.py``
(commit ``a46f14e``) classified multi-company roundup headlines so they could
be *binned* (demoted to specificity score 0).  This module inverts that
decision from "bin" to "route": the same deterministic classification now
decides which of two streams a unique article feeds —

  * **company** — articles specific to the watchlist ticker whose feed
    carried them; consumed by the per-ticker News branch (Plan 3).
  * **macro**   — multi-company roundups, market summaries, and generic
    articles specific to none of their feeds; consumed by the linkage
    analyst (Plan 5) via the ``macro_articles`` session-state key.

Everything remains the shared ``NewsArticle`` model — the macro stream is a
routing destination, not a new schema.  ``MacroArticle`` merely wraps one
article together with the watchlist tickers it mentions.

Design constraints (spec §6.2):
  * Pure and deterministic — no I/O, no config reads, no clock reads, no
    LLM calls.  All tunables arrive as parameters; identical inputs give
    identical outputs, so backtest replays reproduce exactly.
  * Loud failures — malformed inputs raise ``ValueError`` rather than
    silently producing an empty stream.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from data.models import NewsArticle


class MacroArticle(BaseModel):
    """One macro-stream article: the underlying ``NewsArticle`` plus its tags.

    Attributes
    ----------
    article:
        The unmodified shared ``NewsArticle``.  ``article.ticker`` records
        the feed the representative copy was fetched under; for stapled
        roundups this is simply the first feed seen in input order.
    mentioned_tickers:
        Alphabetically-sorted watchlist tickers whose symbol or company-name
        terms appear in the article's headline + summary.  May be empty for
        no-name market summaries ("Risk Is Back On").
    """

    article:           NewsArticle
    mentioned_tickers: list[str] = Field(default_factory=list)


class RoutedArticles(BaseModel):
    """The router's output — one company stream per ticker plus the macro stream.

    Attributes
    ----------
    company:
        Dict keyed by watchlist ticker.  Every watchlist ticker gets a key
        (empty list when nothing routed) so consumers can iterate the
        watchlist without existence checks.  Values preserve input order.
    macro:
        Macro-stream articles in first-appearance input order.
    """

    company: dict[str, list[NewsArticle]]
    macro:   list[MacroArticle]


def build_company_terms(company_name: str | None, symbol: str) -> list[str]:
    """Build the list of lower-cased search terms for one watchlist company.

    The terms produced for a given company are:
      * The ticker symbol (lower-cased), e.g. ``"aapl"``.
      * The full company name (lower-cased), e.g. ``"apple"``.
      * The first word of a multi-word name (lower-cased), e.g. ``"lockheed"``
        for ``"Lockheed Martin"`` — omitted for single-word names since it
        would duplicate the full-name term.

    Parameters
    ----------
    company_name:
        Human-readable name from the watchlist, e.g. ``"Lockheed Martin"``.
        ``None`` or empty string → only the symbol term is returned.
    symbol:
        Ticker symbol, e.g. ``"LMT"``.

    Returns
    -------
    list[str]
        Lower-cased search terms, deduplicated by order of insertion.
    """
    terms: list[str] = [symbol.lower()]

    if company_name:
        full_name = company_name.strip().lower()
        terms.append(full_name)

        # First word of a multi-word name only — single-word names are
        # already fully covered by the full-name term above.
        if " " in full_name:
            first_word = full_name.split()[0]
            if first_word not in terms:
                terms.append(first_word)

    return terms


def count_roundup_companies(
    text: str,
    watchlist_universe: list[dict[str, str]],
) -> int:
    """Count how many distinct watchlist companies are mentioned in ``text``.

    Uses the same term-expansion logic as :func:`build_company_terms` so
    that matching behaviour is consistent across the whole router.  A company
    is counted at most once regardless of how many of its terms appear.

    Parameters
    ----------
    text:
        Lower-cased text to search (typically a headline or headline + summary).
    watchlist_universe:
        Full list of ``{"symbol": ..., "name": ...}`` dicts from the watchlist.

    Returns
    -------
    int
        Number of distinct watchlist companies whose terms appear in ``text``.
    """
    count = 0

    for entry in watchlist_universe:
        terms = build_company_terms(entry.get("name"), entry["symbol"])

        # A company is counted once if ANY of its terms appear in the text.
        if any(term in text for term in terms):
            count += 1

    return count


def _mentioned_tickers(
    text: str,
    watchlist_universe: list[dict[str, str]],
) -> list[str]:
    """Return the sorted watchlist symbols whose terms appear in ``text``.

    Companion to :func:`count_roundup_companies` — same matching semantics,
    but returning *which* companies matched rather than how many.

    Parameters
    ----------
    text:
        Lower-cased text to search (headline + summary combined).
    watchlist_universe:
        Full list of ``{"symbol": ..., "name": ...}`` dicts.

    Returns
    -------
    list[str]
        Alphabetically-sorted matching symbols (sorted for determinism).
    """
    matched: list[str] = []

    for entry in watchlist_universe:
        terms = build_company_terms(entry.get("name"), entry["symbol"])

        if any(term in text for term in terms):
            matched.append(entry["symbol"])

    return sorted(matched)


def route_articles(
    articles: list[NewsArticle],
    watchlist: list[str],
    *,
    company_names: dict[str, str] | None = None,
    roundup_threshold: int = 3,
) -> RoutedArticles:
    """Route pooled watchlist news into company and macro streams.

    Pure and deterministic — no I/O.  Unique stories are identified by URL
    (Finnhub staples one roundup onto many feeds; each copy shares the URL).

    Routing rules per unique article:

    1. **Roundup → macro.**  Headline (or, fallback, headline + summary)
       names ≥ ``roundup_threshold`` distinct watchlist companies.
    2. **Company-specific → company stream.**  Otherwise, each feed ticker
       that carried the article AND whose own symbol/name terms appear in
       the headline or summary keeps its copy.
    3. **Unmatched everywhere → macro.**  Non-roundup articles specific to
       none of their carrying feeds (market summaries, off-topic generics).

    Every macro article is tagged with the sorted watchlist tickers whose
    terms appear in its headline + summary (feed co-occurrence alone is not
    a "mention").

    Parameters
    ----------
    articles:
        Pooled ``NewsArticle`` list across all watchlist feeds, in fetch
        order.  Each article's ``ticker`` records the feed it came from.
    watchlist:
        Watchlist ticker symbols.  Must be non-empty; every article's
        ``ticker`` must appear here.
    company_names:
        Optional symbol → company-name mapping used for term expansion
        (e.g. ``{"AAPL": "Apple"}``).  Without it matching degrades to
        symbols only, which rarely appear in prose — callers should pass
        the watchlist names.
    roundup_threshold:
        Minimum distinct watchlist companies named for an article to be
        classified as a roundup.  Must be ≥ 2.

    Returns
    -------
    RoutedArticles
        ``company`` keyed by every watchlist ticker; ``macro`` in
        first-appearance input order.

    Raises
    ------
    ValueError
        On an empty watchlist, a threshold below 2, or an article whose
        feed ticker is not in the watchlist (wiring bug — loud, not quiet).
    """
    if not watchlist:
        raise ValueError("route_articles: watchlist must not be empty")

    if roundup_threshold < 2:
        raise ValueError(
            f"route_articles: roundup_threshold must be >= 2, got {roundup_threshold}"
        )

    watchlist_set = set(watchlist)
    names         = company_names or {}

    # Universe shape matches what count_roundup_companies has always used.
    universe = [{"symbol": s, "name": names.get(s, "")} for s in watchlist]

    # Loud guard: an article fetched under a non-watchlist feed is a wiring
    # bug in the caller — refusing here beats silently mis-routing it.
    for a in articles:
        if a.ticker not in watchlist_set:
            raise ValueError(
                f"route_articles: article feed ticker {a.ticker!r} is not in "
                f"the watchlist {sorted(watchlist_set)} (url={a.url!r})"
            )

    # ── Group pooled copies of the same story by URL ───────────────────────
    # dict preserves insertion order, so groups iterate in first-appearance
    # order — this fixes the macro stream's output order deterministically.
    # Empty-URL articles cannot be safely grouped; each becomes a singleton.
    groups: dict[str, list[NewsArticle]] = {}

    for idx, a in enumerate(articles):
        key = a.url or f"__no_url_{idx}"
        groups.setdefault(key, []).append(a)

    # Every watchlist ticker gets a company key up front (Plan 3 contract).
    company: dict[str, list[NewsArticle]] = {s: [] for s in watchlist}
    macro:   list[MacroArticle]           = []

    for members in groups.values():
        # All copies share headline/summary text; the first copy in input
        # order is the representative carried into the macro stream.
        rep      = members[0]
        headline = rep.headline.lower()
        summary  = rep.summary.lower()
        combined = headline + " " + summary

        # ── Rule 1: roundup detection (headline first, then combined) ─────
        # Most roundups are self-contained in the headline ("Nvidia, AMD,
        # Tesla Are Big Movers"); the combined fallback catches teaser
        # headlines whose company list spills into the summary.
        is_roundup = (
            count_roundup_companies(headline, universe) >= roundup_threshold
            or count_roundup_companies(combined, universe) >= roundup_threshold
        )

        mentioned = _mentioned_tickers(combined, universe)

        if is_roundup:
            macro.append(MacroArticle(article=rep, mentioned_tickers=mentioned))
            continue

        # ── Rule 2: company-specific routing per carrying feed ────────────
        # Each feed copy stays with its own ticker, but only when that
        # ticker's terms actually appear — a story stapled onto an
        # unmentioned feed must not pollute that ticker's stream.
        routed_to_company = False
        seen_feeds: set[str] = set()

        for member in members:
            ticker = member.ticker

            # One copy per feed — later duplicates under the same feed
            # (shouldn't happen post provider URL-dedup, but stay safe).
            if ticker in seen_feeds:
                continue
            seen_feeds.add(ticker)

            terms = build_company_terms(names.get(ticker), ticker)

            if any(term in combined for term in terms):
                company[ticker].append(member)
                routed_to_company = True

        # ── Rule 3: specific to no carrying feed → macro ───────────────────
        if not routed_to_company:
            macro.append(MacroArticle(article=rep, mentioned_tickers=mentioned))

    return RoutedArticles(company=company, macro=macro)
