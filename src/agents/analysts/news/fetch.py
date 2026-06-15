"""News analyst fetch helpers.

Provides the per-ticker formatting helpers used by ``NewsFetchAgent``
(``agents.analysts.news.fetch_agent``).

The legacy ``news_fetch_callback`` (an ADK ``before_agent_callback``) was
retired in Phase 9 when the per-ticker fan-out design replaced the batched
``NewsAnalyst`` LlmAgent.  Only the formatting helpers remain here so that
``NewsFetchAgent`` can reuse the article-truncation and context-block logic
without duplicating it.

Specificity re-ranking
----------------------
Finnhub's ``/company-news`` endpoint staples broad market-roundup articles
("How the Dow Got to 50,000", "Risk Is Back On") onto large-cap tickers.
In logged backtest inputs, 56–80 % of the top-25 articles per ticker were
off-topic macro stories, drowning the 5–10 genuinely company-specific pieces.

To address this, :func:`_rerank_articles` assigns a **specificity score** to
every article based on whether the ticker symbol or the company name appears
in the headline or summary:

  2 — company-specific, headline match (symbol or name in headline)
  1 — company-specific, summary-only match (symbol or name in summary only)
  0 — generic (neither headline nor summary mentions the company)

Articles are re-ordered ``(score desc, published_at desc)`` so the LLM's
context window is dominated by real company news.  Generic articles are
admitted only up to ``max_generic_articles_per_ticker`` (config default 5)
AND the remaining total-cap budget, whichever is smaller.  A ticker with
zero specific articles receives up to the generic cap — it is never left
empty when articles exist.

Roundup demotion
----------------
A headline (or summary) that names **N or more distinct watchlist companies**
is classified as a macro roundup and demoted to score 0 (generic), regardless
of whether the target ticker appears.  The threshold N is configurable via
``config/analysts.json`` → ``news.roundup_company_threshold`` (default 3).

This addresses a known false-positive: "Nvidia, AMD, Tesla, Apple Are Big
Movers" would previously score 2 ("company-specific") for every ticker it
names, bypassing the generic cap entirely.  Name-dropping in a roundup is
not company-specificity.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from config.analysts import NewsCaps, get_analysts_config
from orchestrator.stock_picker import get_watchlist_with_names

_logger = logging.getLogger(__name__)

# Sentinel value written by providers when no timestamp is available.
_MISSING_SENTINEL = "MISSING_TIMESTAMP"


def _caps() -> NewsCaps:
    """Return the ``NewsCaps`` section from the analysts config.

    Reads caps lazily on first call — avoids running the config loader at
    module import time, which simplifies test isolation.

    Returns
    -------
    NewsCaps
        Validated caps object containing article count caps and
        ``max_summary_chars`` as configured in ``config/analysts.json``.
    """
    return get_analysts_config().news


def _watchlist_universe() -> list[dict[str, str]]:
    """Return the full watchlist as ``{"symbol": ..., "name": ...}`` dicts.

    Delegates to :func:`orchestrator.stock_picker.get_watchlist_with_names`,
    which already handles both the legacy (flat-string) and the extended
    (``{symbol, name}``) watchlist formats.  Called lazily at scoring time so
    that import-time side effects and test isolation are unaffected.

    Returns
    -------
    list[dict[str, str]]
        Each entry has ``"symbol"`` and ``"name"`` keys, in watchlist order.
    """
    return get_watchlist_with_names()


def _parse_published(raw: str | datetime | None) -> datetime | None:
    """Attempt to parse an article's ``published_at`` value into a naive UTC datetime.

    Accepts ISO-format strings (with or without a ``Z`` suffix, with or
    without timezone info), bare ``datetime`` objects (naive or aware), and
    the sentinel value ``"MISSING_TIMESTAMP"``.  Returns ``None`` if the
    value is absent, empty, the sentinel, or unparseable — callers should
    treat ``None`` as *age unknown*.

    Coerces all parsed values to **naive UTC** so callers can safely
    subtract a similarly-coerced ``as_of`` without risking a
    timezone-subtraction ``TypeError``.

    Parameters
    ----------
    raw:
        The raw ``published_at`` field from the article dict/model.

    Returns
    -------
    datetime | None
        A naive UTC ``datetime``, or ``None`` if parsing failed.
    """
    if not raw or raw == _MISSING_SENTINEL:
        return None

    # Already a datetime — strip timezone info if present to get naive UTC.
    if isinstance(raw, datetime):
        if raw.tzinfo is not None:
            return raw.astimezone(UTC).replace(tzinfo=None)
        return raw

    # String path: normalise the "Z" Zulu suffix that fromisoformat rejects
    # on Python < 3.11, then attempt a parse.
    normalised = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalised)
    except (ValueError, TypeError):
        _logger.debug("Unparseable published_at value: %r", raw)
        return None

    # Strip timezone to produce a naive UTC value.
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC).replace(tzinfo=None)
    return parsed


def _build_company_terms(company_name: str | None, symbol: str) -> list[str]:
    """Build the list of search terms for one watchlist company.

    Replicates the same term-expansion logic used throughout the specificity
    scorer so that roundup detection uses identical matching behaviour to the
    existing headline/summary checks.

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


def _count_roundup_companies(
    text: str,
    watchlist_universe: list[dict[str, str]],
) -> int:
    """Count how many distinct watchlist companies are mentioned in ``text``.

    Uses the same term-expansion logic as :func:`_build_company_terms` so
    that matching behaviour is consistent across the whole scorer.  A company
    is counted at most once regardless of how many of its terms appear.

    Parameters
    ----------
    text:
        Lower-cased text to search (typically a headline or headline + summary).
    watchlist_universe:
        Full list of ``{"symbol": ..., "name": ...}`` dicts from the watchlist,
        as returned by :func:`_watchlist_universe`.

    Returns
    -------
    int
        Number of distinct watchlist companies whose terms appear in ``text``.
    """
    count = 0

    for entry in watchlist_universe:
        terms = _build_company_terms(entry.get("name"), entry["symbol"])

        # A company is counted once if ANY of its terms appear in the text.
        if any(term in text for term in terms):
            count += 1

    return count


def _score_article_specificity(
    article: dict,
    ticker: str,
    company_name: str | None,
    *,
    watchlist_universe: list[dict[str, str]] | None = None,
    roundup_threshold: int = 3,
) -> int:
    """Score how company-specific a single article is.

    Assigns a score in {0, 1, 2} based on whether the ticker symbol or the
    company name appears in the article's headline or summary:

      2 — headline match  (symbol OR name found in headline)
      1 — summary-only match  (symbol OR name found in summary, not headline)
      0 — generic  (neither field mentions the company, OR the article is a
                    macro roundup naming ≥ ``roundup_threshold`` distinct
                    watchlist companies)

    **Roundup demotion:** before assigning score 2 or 1, the scorer checks
    whether the headline (and, as a fallback, the combined headline + summary)
    names ``roundup_threshold`` or more distinct watchlist companies.  If so,
    the article is demoted to score 0 regardless of the target ticker's
    presence.  The rationale: listing a ticker alongside five peers in a
    "Big Movers" roundup is not company-specificity — the story is about the
    market, not the company.

    Matching is case-insensitive.  For multi-word company names (e.g.
    "Lockheed Martin") the first word alone is also treated as a match
    ("Lockheed" in a headline is sufficient), reducing false negatives
    from shortened references.  The ticker symbol itself is always searched
    as a plain substring — short symbols (e.g. "AMD") can produce false
    positives, but company-name matching provides the primary signal.

    Parameters
    ----------
    article:
        Article dict.  Supports both ``"title"``/``"headline"`` and
        ``"summary"`` keys (same dual-key lookup used by the renderer).
    ticker:
        Upper-cased ticker symbol, e.g. ``"AAPL"``.
    company_name:
        Human-readable company name from the watchlist, e.g. ``"Apple"``.
        ``None`` or empty string disables name matching (falls back to
        symbol-only matching).
    watchlist_universe:
        Full list of ``{"symbol": ..., "name": ...}`` dicts used for roundup
        detection.  When ``None``, roundup demotion is skipped (safe fallback
        for callers that do not have the universe available, and for tests that
        only exercise the basic score path).
    roundup_threshold:
        Minimum number of distinct watchlist companies that must be named in
        the headline (or headline + summary) for the article to be classified
        as a roundup.  Must be ≥ 2.  Default 3.

    Returns
    -------
    int
        Specificity score in {0, 1, 2}.
    """
    # Extract headline and summary strings, normalising to lower-case for
    # case-insensitive matching throughout.
    headline = (
        article.get("title") or article.get("headline") or ""
    ).lower()

    summary = (article.get("summary") or "").lower()

    # --- Roundup demotion ---------------------------------------------------
    # If the headline alone names enough distinct watchlist companies, the
    # article is a macro roundup — demote to generic regardless of whether
    # the target ticker is among those named.  We also check headline + summary
    # as a combined body in case a short teaser headline spreads its company
    # list across the first sentence of the summary.
    if watchlist_universe and roundup_threshold >= 2:
        # Headline-only check first — most roundups are self-contained in
        # the headline ("Nvidia, AMD, Tesla, Apple Are Big Movers").
        if _count_roundup_companies(headline, watchlist_universe) >= roundup_threshold:
            return 0

        # Fallback: headline + summary combined — catches the pattern where
        # the headline is a teaser ("These Stocks Are Today's Movers:") and
        # the company list spills into the first line of the summary.
        combined = headline + " " + summary
        if _count_roundup_companies(combined, watchlist_universe) >= roundup_threshold:
            return 0

    # --- Standard specificity scoring ---------------------------------------
    # Build the set of search terms to look for in each field.
    # Always include the ticker symbol (e.g. "aapl").
    terms = _build_company_terms(company_name, ticker)

    # Check headline first — a match here earns the highest score.
    if any(term in headline for term in terms):
        return 2

    # No headline match — check summary.
    if any(term in summary for term in terms):
        return 1

    # Neither field contains any identifying term — generic article.
    return 0


def _rerank_articles(
    articles: list,
    ticker: str,
    company_name: str | None,
    *,
    max_total: int,
    max_generic: int,
) -> list:
    """Re-rank articles by specificity and apply the total + generic caps.

    Scores each article via :func:`_score_article_specificity`, then fills
    the output window with specific articles first (score ≥ 1), sorted by
    (score desc, published_at desc), followed by generic articles (score 0)
    sorted by published_at desc.

    Budget rules:
      - Total kept ≤ ``max_total``.
      - Generic articles kept ≤ min(``max_generic``, remaining total budget).
        This means if specific articles already fill the total cap, zero
        generics are included — they do not push the list over the hard cap.
      - A ticker with zero specific articles falls back to up to ``max_generic``
        generics — it is never left empty when articles exist.

    Parameters
    ----------
    articles:
        Raw article list (any order); entries are plain dicts or Pydantic-
        serialised dicts.
    ticker:
        Ticker symbol used for specificity scoring.
    company_name:
        Company name used for specificity scoring (may be ``None``).
    max_total:
        Hard ceiling on the total number of articles returned.
    max_generic:
        Maximum generic (score 0) articles permitted in the output.

    Returns
    -------
    list
        Re-ordered, capped article list.  Each entry is the same object
        (no copies) from the input list.
    """
    # Load the roundup-detection universe and threshold once for the whole
    # batch — avoids repeated config + file reads inside the per-article loop.
    caps            = _caps()
    universe        = _watchlist_universe()
    roundup_thresh  = caps.roundup_company_threshold

    # Score every article and attach the score for sorting.
    scored: list[tuple[int, int, object]] = []

    for article in articles:
        score = _score_article_specificity(
            article,
            ticker,
            company_name,
            watchlist_universe=universe,
            roundup_threshold=roundup_thresh,
        )

        # Extract published_at as a sortable key.  Missing timestamps sort to
        # the oldest position (epoch zero) so they don't displace real articles.
        raw_pub = (
            article.get("published_at") or article.get("date") or ""
            if isinstance(article, dict)
            else getattr(article, "published_at", None) or getattr(article, "date", "") or ""
        )
        published_dt = _parse_published(raw_pub)
        # Use a Unix-style integer timestamp for sorting — missing → 0 (oldest).
        pub_sort = int(published_dt.timestamp()) if published_dt else 0

        scored.append((score, pub_sort, article))

    # Partition into specific (score ≥ 1) and generic (score == 0).
    specific = [(sc, pub, art) for sc, pub, art in scored if sc >= 1]
    generic  = [(sc, pub, art) for sc, pub, art in scored if sc == 0]

    # Sort each partition: primary by score descending, secondary by
    # published_at descending (most recent first).
    specific.sort(key=lambda t: (t[0], t[1]), reverse=True)
    generic.sort(key=lambda t: t[1], reverse=True)

    # Fill the output: specific articles up to max_total.
    chosen = [art for _, _, art in specific[:max_total]]

    # Backfill with generics up to the smaller of the generic cap and the
    # remaining total-cap budget.
    remaining = max_total - len(chosen)
    generic_slots = min(max_generic, remaining)

    if generic_slots > 0:
        chosen.extend(art for _, _, art in generic[:generic_slots])

    return chosen


def _build_ticker_news_context(
    ticker: str,
    articles: list,
    *,
    as_of: datetime,
    company_name: str | None = None,
) -> str:
    """Build the LLM-readable context block for a single ticker's news.

    Formats headlines and article summaries into a text block suitable for
    direct inclusion in an LLM prompt.  Before rendering, articles are
    **re-ranked by specificity** via :func:`_rerank_articles` so that
    company-specific stories fill the window first and generic macro
    roundups are capped at ``max_generic_articles_per_ticker``.

    Only the most recent ``max_articles_per_ticker`` articles (after
    re-ranking) are included; summaries are truncated to ``max_summary_chars``
    characters to control token usage.  Both caps are read from
    ``config/analysts.json`` via :func:`_caps`.

    An ``As of:`` anchor line is rendered immediately after the ticker
    header so the LLM knows the exact reference date.  Each article shows
    its publication date *and* its age in whole days relative to ``as_of``
    (e.g. ``[2025-10-10, 5d ago]``), enabling the model to reason about
    whether a story is still fresh or has likely been priced in already.

    Parameters
    ----------
    ticker:
        Ticker symbol label.
    articles:
        List of article dicts (serialised ``NewsArticle`` instances) or raw
        dict-like objects from the provider.
    as_of:
        The historical clock value for this tick.  Used as the reference
        point for computing per-article age.  May be tz-aware or naive;
        the helper normalises internally.
    company_name:
        Human-readable company name from the watchlist (e.g. ``"Apple"``).
        Used by the specificity scorer to identify company-specific
        articles; ``None`` falls back to symbol-only matching.

    Returns
    -------
    str
        A formatted text block ready for concatenation into ``news_context``.
    """
    # Normalise as_of to naive UTC for consistent age arithmetic.
    as_of_naive = (
        as_of.astimezone(UTC).replace(tzinfo=None)
        if as_of.tzinfo is not None
        else as_of
    )

    lines: list[str] = [f"=== {ticker} ==="]

    # Render the reference anchor so the LLM knows what "now" is.
    lines.append(f"  As of: {as_of_naive.date().isoformat()}")

    if not articles:
        lines.append("  (no news available)")
        return "\n".join(lines)

    # Read caps from config — done once per call, not per article.
    caps = _caps()

    # Re-rank articles by specificity, capping generic (macro) articles so
    # they cannot crowd out company-specific stories.
    selected = _rerank_articles(
        articles,
        ticker,
        company_name,
        max_total=caps.max_articles_per_ticker,
        max_generic=caps.max_generic_articles_per_ticker,
    )

    for i, article in enumerate(selected, start=1):
        # Support both dict access and attribute access depending on how the
        # provider serialised the NewsArticle.
        if isinstance(article, dict):
            headline  = article.get("title") or article.get("headline") or "(no title)"
            summary   = (article.get("summary") or "").strip()
            published = article.get("published_at") or article.get("date") or ""
        else:
            headline  = getattr(article, "title", None) or getattr(article, "headline", "(no title)")
            summary   = (getattr(article, "summary", None) or "").strip()
            published = getattr(article, "published_at", None) or getattr(article, "date", "") or ""

        # Attempt to compute article age relative to the as_of reference point.
        published_dt = _parse_published(published)

        if published_dt is not None:
            # Clamp negative ages (dirty future-dated data) to 0 — all
            # fetched articles are PIT-correct (<= as_of), so negatives
            # only arise from data hygiene issues.
            age_days  = max(0, (as_of_naive - published_dt).days)
            age_str   = f"{age_days}d ago"
            date_part = str(published)[:10]  # ISO date prefix (YYYY-MM-DD)
            date_str  = f" [{date_part}, {age_str}]"
        elif published:
            # We have a raw value but could not parse it — show it as-is
            # so the LLM still sees some date information.
            date_str = f" [{published}, age unknown]"
        else:
            # No publication date at all.
            date_str = " [age unknown]"

        lines.append(f"  [{i}]{date_str} {headline}")

        if summary:
            # Truncate to avoid token bloat while preserving the key content.
            lines.append(f"       {summary[:caps.max_summary_chars]}")

    return "\n".join(lines)
