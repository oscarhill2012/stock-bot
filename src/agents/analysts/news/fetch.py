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

Dedup + recency sort
--------------------
A single story is often syndicated across dozens of outlets, producing
near-identical articles that flood the LLM context and make stale news
appear voluminous.  :func:`_dedup_and_sort_articles` addresses both defects:

1. **Dedup** — normalises each article's title (lower-case, strip
   punctuation and extra whitespace), then groups articles whose normalised
   titles are similar enough (measured by ``difflib.SequenceMatcher``) into
   clusters.  From each cluster, only the freshest article is kept.  The
   similarity threshold is config-driven (``news.dedup_title_similarity_threshold``,
   default 0.85) so it can be tightened to exact-match-only (1.0) or loosened
   for aggressive dedup (0.7) without touching source code.

2. **Recency sort** — surviving articles (one per cluster) are sorted
   freshest-first by ``published_at``.  Articles with unparseable timestamps
   sort last (so they don't displace datable articles), but if the entire
   non-empty input fails to parse, a ``ValueError`` is raised rather than
   silently producing an empty block.

The dedup pass runs **before** specificity re-ranking so that the raw article
count that feeds into ``_rerank_articles`` is already de-duplicated.  Numeric
features (mention counts etc.) that are derived elsewhere from the raw article
list are **not** changed here — dedup applies only to the LLM-facing render
path.  If a future feature needs dedup-aware counts, that should be wired
explicitly and flagged for review.
"""
from __future__ import annotations

import logging
import re as _re
import unicodedata
from datetime import UTC, datetime
from difflib import SequenceMatcher
from hashlib import blake2b

from agents.analysts.news.history import NewsHistoryStore

# Classification helpers moved to ``agents.analysts.news.router`` (Phase 14
# Plan 2) where they back the company/macro stream router.  Re-exported here
# under their historical private names so ``_score_article_specificity`` and
# the existing test suite keep working unchanged — Plan 3's rebuild owns any
# further restructure of this module.
from agents.analysts.news.router import build_company_terms as _build_company_terms
from agents.analysts.news.router import count_roundup_companies as _count_roundup_companies
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


def _parse_published(raw: object) -> datetime | None:
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
        The raw ``published_at`` field from the article dict/model — typed
        ``object`` because callers may pass through an untyped value (e.g.
        from ``_article_fields``); expected at runtime to be a ``str``,
        ``datetime``, or ``None``.

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


# Sort anchor for articles whose publication time cannot be parsed — epoch
# zero sorts them first so they can never displace a datable original.
_EPOCH_ZERO = datetime(1970, 1, 1)


def _article_fields(article: object) -> tuple[str, str, object]:
    """Extract ``(headline, summary, raw_published)`` from an article.

    Centralises the dual dict/model access pattern used throughout this
    module so every consumer reads fields identically.  ``raw_published``
    is returned unparsed (str, datetime, or None) — callers hand it to
    ``_parse_published`` when they need a datetime.

    Parameters
    ----------
    article:
        Serialised dict or ``NewsArticle``-shaped object.

    Returns
    -------
    tuple[str, str, object]
        ``(headline, stripped_summary, raw_published)``.
    """
    if isinstance(article, dict):
        headline = article.get("title") or article.get("headline") or ""
        summary = (article.get("summary") or "").strip()
        published = article.get("published_at") or article.get("date")
    else:
        headline = (
            getattr(article, "title", None)
            or getattr(article, "headline", None)
            or ""
        )
        summary = (getattr(article, "summary", None) or "").strip()
        published = (
            getattr(article, "published_at", None)
            or getattr(article, "date", None)
        )

    return str(headline), str(summary), published


def article_key(article: object) -> str:
    """Stable identity key for one article across ticks and re-fetches.

    Prefers the provider URL (unique per story, stable across fetches).
    URL-less articles fall back to a digest of headline + raw timestamp so
    two same-headline stories on different days do not collide.

    Parameters
    ----------
    article:
        Serialised dict or ``NewsArticle``-shaped object.

    Returns
    -------
    str
        The URL, or ``"hash:<blake2b-digest>"`` when no URL is present.
    """
    headline, _summary, raw_published = _article_fields(article)

    if isinstance(article, dict):
        url = article.get("url") or ""
    else:
        url = getattr(article, "url", None) or ""

    if url:
        return str(url)

    digest = blake2b(
        f"{headline}|{raw_published}".encode(), digest_size=12,
    ).hexdigest()
    return f"hash:{digest}"


async def partition_articles_by_staleness(
    ticker: str,
    articles: list,
    *,
    store: NewsHistoryStore,
    threshold: float,
) -> tuple[list, list]:
    """Split ``articles`` into (fresh, stale) via the history store.

    This is the deterministic staleness pre-filter that replaced the
    heuristic specificity re-ranker (Phase 14): an article is STALE when it
    was seen on an earlier tick (identity match) or when its text scores at
    or above ``threshold`` cosine similarity against anything previously
    recorded for the ticker (Tetlock rehash measure).  Everything else is
    FRESH — a genuine-surprise candidate for the LLM.

    Articles are processed oldest-first so that, within a single tick, the
    first copy of a syndicated story is judged (and recorded) before its
    rehashes — later copies then measure similar and land in the stale
    bucket.  Every judged article is recorded, including stale ones, so
    the next tick's re-fetch short-circuits on identity without a fresh
    embedding call.

    Parameters
    ----------
    ticker:
        Namespace for the history store (the ticker symbol).
    articles:
        Serialised article dicts (or model objects) for one ticker.
    store:
        The per-run ``NewsHistoryStore``.
    threshold:
        Cosine-similarity cut-off from
        ``config/analysts.json::staleness_similarity_threshold``.

    Returns
    -------
    tuple[list, list]
        ``(fresh, stale)`` — two lists in oldest-first order.

    Raises
    ------
    Exception
        Whatever the store's embedding function raises — failures are loud.
    """
    caps = _caps()

    def _published_or_epoch(article: object) -> datetime:
        """Sort key: parsed publication time, or epoch zero when unknown."""
        _headline, _summary, raw_published = _article_fields(article)
        return _parse_published(raw_published) or _EPOCH_ZERO

    ordered = sorted(articles, key=_published_or_epoch)

    fresh: list = []
    stale: list = []

    for article in ordered:
        headline, summary, raw_published = _article_fields(article)
        key = article_key(article)

        # Exact-identity short-circuit: seen on an earlier tick means stale
        # by definition — no embedding spend.
        if store.has(ticker, key):
            stale.append(article)
            continue

        # Tetlock measure: embed headline + capped summary, compare against
        # everything previously recorded for this ticker.
        text = f"{headline}. {summary[: caps.max_summary_chars]}".strip()
        similarity = await store.staleness(ticker, text)

        # Record BEFORE classifying so same-tick rehashes compare against
        # this article too.  Stale articles are recorded as well — their
        # key then short-circuits the next tick's re-fetch.
        await store.record(
            ticker,
            key,
            text,
            published_at=_parse_published(raw_published) or _EPOCH_ZERO,
        )

        if similarity >= threshold:
            stale.append(article)
        else:
            fresh.append(article)

    return fresh, stale


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


def _normalise_title(title: str) -> str:
    """Produce a canonical form of an article title for similarity comparison.

    Applies the following transforms in order so that superficial
    syndication differences (capitalisation, trailing source tags, stray
    punctuation) do not prevent near-duplicate detection:

    1. Unicode NFKC normalisation — collapses compatibility characters (e.g.
       curly quotes, en-dashes) to their ASCII equivalents.
    2. Lower-case the result.
    3. Strip any trailing parenthetical source attribution such as
       "(Reuters)", "(AP)", "(Bloomberg)" — the first 30 characters of a
       match are removed.
    4. Remove all punctuation characters (anything that is not a letter,
       digit, or whitespace).
    5. Collapse runs of whitespace to a single space and strip leading /
       trailing space.

    Parameters
    ----------
    title:
        Raw article title string.

    Returns
    -------
    str
        Normalised title, suitable for ``SequenceMatcher`` comparison.
    """
    # Step 1: Unicode NFKC → normalises fancy quotes, dashes, etc.
    text = unicodedata.normalize("NFKC", title)

    # Step 2: lower-case for case-insensitive matching.
    text = text.lower()

    # Step 3: strip trailing parenthetical source tags, e.g. "(reuters)".
    # The pattern matches a parenthetical at the very end of the string
    # (with optional trailing whitespace).  We cap the group length at 40
    # characters so we don't accidentally strip substantive title content.
    text = _re.sub(r"\s*\([^)]{1,40}\)\s*$", "", text)

    # Step 4: remove all non-alphanumeric, non-space characters.
    text = _re.sub(r"[^\w\s]", " ", text)

    # Step 5: collapse internal whitespace.
    text = " ".join(text.split())

    return text


def _title_similarity(normalised_a: str, normalised_b: str) -> float:
    """Compute the similarity ratio between two normalised title strings.

    Uses ``difflib.SequenceMatcher`` on the character level — the ratio is
    the standard ``2 × matching_chars / total_chars`` value in [0.0, 1.0].

    As a special case, two *empty* normalised titles are considered identical
    (ratio 1.0) — this handles articles with no title gracefully.

    Parameters
    ----------
    normalised_a:
        First normalised title string.
    normalised_b:
        Second normalised title string.

    Returns
    -------
    float
        Similarity ratio in [0.0, 1.0].  1.0 = identical; 0.0 = no common
        characters.
    """
    if not normalised_a and not normalised_b:
        # Both empty — treat as identical (same "no title" article class).
        return 1.0

    return SequenceMatcher(None, normalised_a, normalised_b, autojunk=False).ratio()


def _dedup_and_sort_articles(articles: list) -> list:
    """Deduplicate near-identical articles and sort survivors freshest-first.

    This is the **data-quality pre-processing step** that runs before
    specificity re-ranking.  It addresses two structural defects in the raw
    article list returned by the news provider:

    1. **Rehash flooding** — a single story is syndicated to many outlets;
       each outlet counts as a separate article.  Without dedup, 100 copies
       of the same headline crowd the LLM context and make one old story
       look voluminous and important (AMD 2025-09-22 observed 116 stale
       rehashes vs 16 fresh distinct stories).

    2. **No recency order** — providers return articles in arbitrary order.
       Surfacing freshest stories first lets the LLM triage recency without
       having to hunt through the full list.

    Algorithm
    ---------
    1. Parse each article's ``published_at`` field via :func:`_parse_published`.
    2. Raise ``ValueError`` if the article list is non-empty but **every**
       article failed to parse a timestamp — this is a data-hygiene error
       that should be visible, not silently collapsed to an empty render.
    3. Group articles into clusters using a greedy single-pass algorithm:
       - Compute the normalised title via :func:`_normalise_title`.
       - Compare the normalised title against each existing cluster's
         representative normalised title using :func:`_title_similarity`.
       - If the similarity ≥ ``dedup_title_similarity_threshold`` (from
         ``config/analysts.json``), the article joins that cluster.
       - Otherwise it starts a new cluster of its own.
    4. For each cluster, keep the **freshest** article (highest
       ``published_at`` timestamp); articles with unparseable timestamps
       are treated as epoch-zero (oldest) for representative selection,
       so they can only win a cluster if every other member also has an
       unparseable timestamp.
    5. Sort the surviving cluster representatives freshest-first;
       tie-break by original input index (stable / deterministic — no
       random ordering, so backtest runs reproduce).

    Design notes
    ------------
    - This function deliberately does **not** apply the total or generic
      article caps — those are the responsibility of :func:`_rerank_articles`
      which runs after this pass.
    - Numeric features derived from the raw article list elsewhere (e.g.
      ``mention_count`` in the extractor layer) are NOT affected by this
      function — it only touches the list that reaches the LLM renderer.
      This is intentional: changing extractor counts would alter the
      feature space and requires a separate review.

    Parameters
    ----------
    articles:
        Raw article list (dicts or Pydantic-serialised objects) in any order.

    Returns
    -------
    list
        Deduplicated, freshest-first article list.  Each entry is the
        original article object (no copies).

    Raises
    ------
    ValueError
        If ``articles`` is non-empty but every article's timestamp is
        unparseable (sentinel, ``None``, or garbage string).  This
        distinguishes a genuine "no news" situation (empty input) from a
        parse-failure scenario that would otherwise silently produce the
        same empty-looking context block.
    """
    if not articles:
        # Genuinely empty — no news available for this ticker.
        return []

    # --- Step 1: Parse timestamps for all articles ----------------------------
    def _get_raw_pub(article: object) -> str | None:
        """Extract the raw ``published_at`` value from a dict or model object."""
        if isinstance(article, dict):
            return article.get("published_at") or article.get("date") or ""
        return getattr(article, "published_at", None) or getattr(article, "date", "") or ""

    # Build a list of (article, parsed_datetime_or_None) pairs.  Articles
    # whose timestamps cannot be parsed receive ``None`` and sort last (epoch-
    # zero sentinel), but they are NOT dropped — they still reach the renderer
    # and appear as "age unknown" in the context block.
    #
    # We do raise if ALL timestamps are unparseable AND the title extraction
    # also fails entirely (all titles empty after normalisation) — that
    # degenerate scenario would collapse every article into one cluster
    # representative with no meaningful content, hiding a systematic data
    # failure behind a silent single-entry output.
    parsed_articles: list[tuple[object, datetime | None]] = []
    parse_success_count = 0

    for article in articles:
        raw_pub = _get_raw_pub(article)
        dt      = _parse_published(raw_pub)

        if dt is not None:
            parse_success_count += 1

        parsed_articles.append((article, dt))

    # Loud failure: non-empty input where every timestamp is unparseable AND
    # there are no titles at all — this is a systematically broken feed, not
    # normal "age unknown" articles.  Normal articles with missing timestamps
    # render fine with "age unknown", so we only raise when the combination of
    # no-timestamps AND no-titles indicates a completely degenerate payload.
    if parse_success_count == 0:
        def _has_title(article: object) -> bool:
            """Return True if the article has a non-empty title or headline."""
            if isinstance(article, dict):
                return bool(article.get("title") or article.get("headline"))
            return bool(
                getattr(article, "title", None) or getattr(article, "headline", None)
            )

        if not any(_has_title(a) for a in articles):
            raise ValueError(
                f"_dedup_and_sort_articles: {len(articles)} article(s) provided but "
                f"every timestamp was unparseable AND no article has a title — "
                f"this appears to be a systematically broken feed.  "
                f"First article raw timestamp: {_get_raw_pub(articles[0])!r}"
            )

        # All timestamps missing but articles have titles — this is the valid
        # "age unknown" path.  Log a warning and proceed; the renderer will
        # show "age unknown" for each article.
        _logger.warning(
            "_dedup_and_sort_articles: all %d articles have unparseable timestamps; "
            "rendering with 'age unknown' labels.  Check provider timestamp format.",
            len(articles),
        )

    # --- Step 3: Greedy cluster assignment ------------------------------------
    # Read the similarity threshold from config — called lazily so test mocks
    # of ``_caps`` are already in place when this line runs.
    threshold = _caps().dedup_title_similarity_threshold

    def _get_title(article: object) -> str:
        """Extract the title/headline from a dict or model object."""
        if isinstance(article, dict):
            return article.get("title") or article.get("headline") or ""
        return getattr(article, "title", None) or getattr(article, "headline", None) or ""

    # Each cluster is a list of (article, dt_or_None, original_index) tuples.
    # We track the normalised representative title for O(1) comparisons.
    clusters: list[tuple[str, list[tuple[object, datetime | None, int]]]] = []

    for idx, (article, dt) in enumerate(parsed_articles):
        normalised = _normalise_title(_get_title(article))

        # Try to find an existing cluster whose representative is similar.
        matched_cluster_idx: int | None = None

        for cluster_idx, (rep_normalised, _members) in enumerate(clusters):
            if _title_similarity(normalised, rep_normalised) >= threshold:
                matched_cluster_idx = cluster_idx
                break

        if matched_cluster_idx is not None:
            # Append to the existing cluster.
            clusters[matched_cluster_idx][1].append((article, dt, idx))
        else:
            # Start a new cluster with this article as the representative.
            clusters.append((normalised, [(article, dt, idx)]))

    # --- Step 4: Choose the freshest representative from each cluster ---------
    # Epoch-zero sentinel for articles with no parseable timestamp — they sort
    # last and can only win if every other member of the cluster also has no
    # timestamp.
    _EPOCH_ZERO = datetime(1970, 1, 1)

    survivors: list[tuple[object, datetime | None, int]] = []

    for _rep_normalised, members in clusters:
        # Sort members by (timestamp desc, original_index asc) — the first
        # sort key picks the freshest; the second provides a stable tie-break
        # that reproduces across calls (no hash-dependent ordering).
        best = max(
            members,
            key=lambda m: (m[1] or _EPOCH_ZERO, -m[2]),
        )
        survivors.append(best)

    # --- Step 5: Sort survivors freshest-first --------------------------------
    # Tie-break by original input index (ascending) for determinism.
    survivors.sort(key=lambda m: (m[1] or _EPOCH_ZERO, -m[2]), reverse=True)

    return [article for article, _dt, _idx in survivors]


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
    processed through two successive passes:

    1. **Dedup + recency sort** (:func:`_dedup_and_sort_articles`) — collapses
       near-identical syndication rehashes into one representative (keeping the
       freshest), then sorts surviving articles freshest-first.  This prevents
       a single story published by 100 outlets from dominating the context
       window and making stale news look voluminous.

    2. **Specificity re-ranking** (:func:`_rerank_articles`) — demotes generic
       macro roundup articles so company-specific stories fill the context
       window first.  The total and generic caps are applied here.

    Only the most recent ``max_articles_per_ticker`` articles (after both
    passes) are included; summaries are truncated to ``max_summary_chars``
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

    # Pass 1: dedup near-identical syndication rehashes and recency-sort.
    # This reduces a 100-article rehash flood to a single representative and
    # surfaces the freshest stories first.  The ValueError from all-unparseable
    # timestamps intentionally propagates — it should be loud.
    deduped = _dedup_and_sort_articles(articles)

    # Pass 2: re-rank by specificity, capping generic (macro) articles so
    # they cannot crowd out company-specific stories.
    selected = _rerank_articles(
        deduped,
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
