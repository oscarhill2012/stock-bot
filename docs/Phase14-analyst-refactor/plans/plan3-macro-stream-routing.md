# Plan 3 — Macro Stream Data Plumbing (Router + Refetch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing roundup-demotion classification in the news pipeline into a deterministic router that splits pooled watchlist news into a per-ticker **company** stream and a ticker-tagged **macro** stream, emit the macro stream into ADK session state as `state["macro_articles"]`, and make the golden-cache news path (per-tick caps + window refetch) parity-safe so backtest and live see identical routed inputs.

> **Split-value note.** This plan delivers two independently valuable halves. The **company/macro router split** and the **cache parity fixes** stand on their own — they sharpen the per-ticker news stream (Plan 2) and hold regardless of what happens downstream. The **`state["macro_articles"]` emission**, by contrast, has exactly one consumer: Plan 4's linkage analyst, whose build is **gated on Plan 2's eval** (spec §3, §6.4). If that gate does not open, the macro key is written each tick but read by nothing — a benign, cheap orphan (a serialised list on a state key), **not** a reason to drop the emission from this plan. Keep it: it is the seam Plan 4 plugs into, and leaving it in place means linkage lands as pure addition with no retrofit to Plan 3. Do **not** add speculative consumers or defensive coupling for a linkage stream that may never be built.

**Architecture:** A new pure module `src/agents/analysts/news/router.py` owns the classification (moved out of `news/fetch.py`, which keeps thin delegating aliases — Plan 2 owns any further restructure of `fetch.py`). `NewsFetchAgent` pools each tick's per-ticker fetches, calls `route_articles`, and writes the serialised macro stream to the (non-`temp:`) `macro_articles` state key. The news article caps that currently live as hardcoded values (dispatcher default `50`, backfill `9000`) move to `config/data.json` and are applied identically by the live dispatcher and the backtest cache provider. Finally, the target windows are refetched (`--refetch-domain news`) so cached responses include roundups previously discarded at old cap margins.

**Tech Stack:** Python 3.12, Pydantic v2, Google ADK (`BaseAgent` state_delta events), SQLite golden cache (SQLAlchemy), pytest + pytest-asyncio.

## Global Constraints

Every task's requirements implicitly include this section. Copied from the approved spec (`docs/Phase14-analyst-refactor/specs/analyst-drift-refactor-design.md`) and the cross-plan pins:

- **D1 — No new news providers.** Finnhub `/company-news` is the sole news source. Do not add providers or touch `config/data.json` `providers.news`.
- **D2 — Backtest/live parity is non-negotiable.** The Finnhub general-news feed (`/news?category=…`) must not appear anywhere. Live and backtest consume the identical endpoint with identical routing.
- **D3 — Golden-cache refetch is permitted** to populate the macro stream for existing windows.
- **Plan 3 is deterministic only — no LLM calls anywhere in this plan.**
- **Pinned cross-plan interfaces (names verbatim, do not rename):** `src/agents/analysts/news/router.py` with `class MacroArticle` (the underlying `NewsArticle` plus `mentioned_tickers: list[str]`), `def route_articles(articles: list[NewsArticle], watchlist: list[str]) -> RoutedArticles`, `RoutedArticles.company: dict[str, list[NewsArticle]]` (keyed by ticker), `RoutedArticles.macro: list[MacroArticle]`. Session-state key: `state["macro_articles"]` — a list of serialised `MacroArticle` dicts with datetimes ISO-stringified.
- **Co-planned siblings (trust, no defensive shims):** Plan 2 rebuilds `fetch.py` internals and will call `route_articles` and consume `.company`; Plan 4 reads `state["macro_articles"]`. Keep `fetch.py` touchpoints MINIMAL — extraction leaves thin delegating aliases behind; do not restructure `fetch.py`.
- **PIT rules:** the Finnhub provider's `to_date` clipping and response-side PIT filter (`src/data/providers/news/finnhub.py`) must be preserved untouched. Every datetime written to ADK state is ISO-stringified first (`model_dump(mode="json")`) — the backtest `DatabaseSessionService` cannot hold `datetime`.
- **Config convention:** every new tunable goes in `config/data.json` + a `config/README.md` row in the same task. Never hardcode.
- **House style:** British English everywhere; every function gets a docstring (purpose, parameters, return); non-trivial logic gets inline comments; blank lines between logical blocks.
- **Loud failures:** raise over silent degradation. Tests assert **positive routing** (a fixture roundup ARRIVES in `.macro` with correct ticker tags), never just absence of errors.
- **Shell conventions:** never prefix commands with `cd`. Tests: `.venv/bin/python -m pytest tests/... -v`. Scripts: `PYTHONPATH=src .venv/bin/python -m scripts.<name>`.
- **Git quirk:** new files under `tests/unit/data/` are silently ignored by `.git/info/exclude` — stage them with `git add -f`.
- Commit messages end with: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`

## Empirical facts the plan relies on (measured 2026-07-06)

- `backtests/baseline-2025-09/store.sqlite`: 16,823 news rows across 20 tickers; busiest 7-day per-ticker volume **318 articles (NVDA)**; **2,845 URLs shared by ≥ 2 tickers** (cross-ticker stapled roundups are real and partially cached already).
- Live per-tick news reads cap at the dispatcher's hardcoded `limit=50` (`src/data/__init__.py:160`) while the backtest cache provider ignores `limit` entirely and returns the whole lookback window — an existing parity gap that becomes a routing correctness bug once roundups matter. Fixed in Task 4.
- The cache-fill in `scripts/backtest_fetch.py::_news` passes a hardcoded `limit=9000` (its docstring stale-claims `2000`). Older fills ran under smaller caps, so refetch (Task 5) repopulates the roundups discarded at those margins.

---

### Task 1: Router module — pure classification and routing

**Files:**
- Create: `src/agents/analysts/news/router.py`
- Test: `tests/unit/agents/analysts/news/test_router.py`

**Interfaces:**
- Consumes: `data.models.NewsArticle` (existing shared Pydantic model — `ticker`, `headline`, `summary`, `url`, `source`, `published_at`, `sentiment`, `relevance`).
- Produces (pinned, consumed by Plans 2 and 4 and by Tasks 2–3 and 5 of this plan):
  - `class MacroArticle(BaseModel)` with fields `article: NewsArticle` and `mentioned_tickers: list[str]`.
  - `class RoutedArticles(BaseModel)` with fields `company: dict[str, list[NewsArticle]]` (a key for **every** watchlist ticker, empty list when nothing routed) and `macro: list[MacroArticle]`.
  - `def route_articles(articles: list[NewsArticle], watchlist: list[str], *, company_names: dict[str, str] | None = None, roundup_threshold: int = 3) -> RoutedArticles` — pure, deterministic, no I/O. The two positional parameters are the pinned signature; the keyword-only extras are optional tuning the caller sources from config (`company_names` from the watchlist, `roundup_threshold` from `config/analysts.json → news.roundup_company_threshold`).
  - `def build_company_terms(company_name: str | None, symbol: str) -> list[str]` and `def count_roundup_companies(text: str, watchlist_universe: list[dict[str, str]]) -> int` — the classification helpers moved verbatim from `fetch.py` (Task 2 re-exports them there under their historical `_`-prefixed names).

**Routing semantics (locked in by the tests below):**

1. Articles are pooled across all watchlist feeds; the same story appears under several tickers with the same URL, so unique articles are identified by URL (empty-URL articles are never grouped — each is its own singleton).
2. A unique article whose headline (or, fallback, headline + summary) names ≥ `roundup_threshold` distinct watchlist companies is a **roundup → macro** (this is commit `a46f14e`'s demotion logic, inverted from "bin" to "route").
3. A non-roundup article routes to `company[t]` for each feed ticker `t` that carried it **and** whose own symbol/name terms appear in the headline or summary (company-specific, as now).
4. A non-roundup article specific to **none** of the feeds that carried it (market summaries such as "Risk Is Back On", or off-topic generic pieces) → **macro**.
5. Every macro article is tagged `mentioned_tickers` = the alphabetically-sorted watchlist tickers whose terms appear in its headline + summary (may be empty for no-name market summaries). Feed co-occurrence does **not** count as a mention — the spec wording is "the watchlist tickers they mention".
6. Loud failures: empty `watchlist`, `roundup_threshold < 2`, or an article whose `ticker` is not in `watchlist` all raise `ValueError`.
7. Determinism: `company` keys follow watchlist order; per-ticker lists and `macro` follow first-appearance input order; `mentioned_tickers` is sorted. No randomness, no config reads, no clock reads.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/agents/analysts/news/test_router.py` with exactly this content:

```python
"""Unit tests for the specificity router (Phase 14 Plan 3).

The router is a pure function: pooled watchlist news in, a company stream
(per-ticker, company-specific) and a macro stream (roundups / market
summaries, tagged with mentioned watchlist tickers) out.  These tests lock
in the routing semantics that Plans 2 and 4 build on, and they assert
POSITIVE routing — fixture roundups must ARRIVE in ``.macro`` with the
correct ticker tags, not merely fail to error.
"""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from agents.analysts.news.router import (
    MacroArticle,
    RoutedArticles,
    build_company_terms,
    count_roundup_companies,
    route_articles,
)
from data.models import NewsArticle

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

# A small fixed universe — symbols plus human names, mirroring the shape of
# ``orchestrator.stock_picker.get_watchlist_with_names`` output.
WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA"]
NAMES     = {"AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "Nvidia", "TSLA": "Tesla"}


def _article(
    ticker: str,
    headline: str,
    *,
    summary: str = "",
    url: str | None = None,
    published: str = "2026-02-10T14:00:00",
) -> NewsArticle:
    """Build a minimal valid ``NewsArticle`` for router tests.

    Parameters
    ----------
    ticker:
        Feed ticker the article was fetched under.
    headline:
        Article headline text.
    summary:
        Optional summary body.
    url:
        Explicit URL; when omitted a deterministic slug is derived from the
        ticker + headline so distinct fixtures never accidentally group.
    published:
        ISO timestamp string for ``published_at``.

    Returns
    -------
    NewsArticle
        A fully-populated article model.
    """
    slug = re.sub(r"\W+", "-", f"{ticker}-{headline}".lower()).strip("-")

    return NewsArticle(
        ticker       = ticker,
        headline     = headline,
        summary      = summary,
        url          = url or f"https://example.com/{slug}",
        source       = "test",
        published_at = datetime.fromisoformat(published),
    )


def _route(articles: list[NewsArticle], **overrides) -> RoutedArticles:
    """Call ``route_articles`` with the shared fixture universe.

    Parameters
    ----------
    articles:
        Pooled article list.
    **overrides:
        Optional keyword overrides for ``route_articles`` (e.g.
        ``company_names=None`` to test symbol-only matching).

    Returns
    -------
    RoutedArticles
        The routing result.
    """
    kwargs = {"company_names": NAMES, "roundup_threshold": 3}
    kwargs.update(overrides)

    return route_articles(articles, WATCHLIST, **kwargs)


# ---------------------------------------------------------------------------
# (a) Roundup → macro, with correct ticker tags (positive-signal tests)
# ---------------------------------------------------------------------------

def test_roundup_headline_routes_to_macro_with_ticker_tags():
    """A ≥-threshold multi-company headline ARRIVES in .macro, tagged and sorted."""
    art = _article("NVDA", "Nvidia, Tesla and Apple are today's big movers")

    routed = _route([art])

    assert len(routed.macro) == 1
    assert routed.macro[0].article.url == art.url
    assert routed.macro[0].mentioned_tickers == ["AAPL", "NVDA", "TSLA"]

    # And it must NOT leak into any company stream.
    assert all(v == [] for v in routed.company.values())


def test_teaser_headline_with_companies_in_summary_is_roundup():
    """The headline+summary fallback catches teaser headlines with the list in the body."""
    art = _article(
        "AAPL",
        "These stocks are moving today",
        summary="Apple, Microsoft and Nvidia all jumped in early trading.",
    )

    routed = _route([art])

    assert len(routed.macro) == 1
    assert routed.macro[0].mentioned_tickers == ["AAPL", "MSFT", "NVDA"]
    assert all(v == [] for v in routed.company.values())


def test_market_summary_with_no_mentions_routes_to_macro():
    """A no-name market summary is macro with an empty mention tag."""
    art = _article("AAPL", "Risk is back on as stocks rally to fresh highs")

    routed = _route([art])

    assert len(routed.macro) == 1
    assert routed.macro[0].mentioned_tickers == []
    assert routed.company["AAPL"] == []


# ---------------------------------------------------------------------------
# (b) Company-specific → per-ticker stream
# ---------------------------------------------------------------------------

def test_company_specific_routes_to_feed_ticker():
    """A single-company article stays in its feed ticker's company stream."""
    art = _article("AAPL", "Apple unveils new iPhone with on-device AI")

    routed = _route([art])

    assert routed.company["AAPL"] == [art]
    assert routed.macro == []


def test_cross_feed_duplicate_collapses_to_mentioning_feed():
    """A story stapled onto two feeds routes only to the ticker it actually mentions."""
    a_aapl = _article("AAPL", "Apple beats expectations", url="https://example.com/shared-1")
    a_msft = _article("MSFT", "Apple beats expectations", url="https://example.com/shared-1")

    routed = _route([a_aapl, a_msft])

    # AAPL's copy survives in AAPL's stream; MSFT's stapled copy is not
    # MSFT-specific and, being neither roundup nor unmatched-everywhere,
    # simply does not pollute MSFT's stream.
    assert routed.company["AAPL"] == [a_aapl]
    assert routed.company["MSFT"] == []
    assert routed.macro == []


def test_two_mentions_below_threshold_routes_to_both_feeds():
    """Two named companies (< threshold) is not a roundup — both mentioned feeds keep it."""
    a_aapl = _article("AAPL", "Apple sues Nvidia over chip patents", url="https://example.com/suit-1")
    a_nvda = _article("NVDA", "Apple sues Nvidia over chip patents", url="https://example.com/suit-1")

    routed = _route([a_aapl, a_nvda])

    assert routed.company["AAPL"] == [a_aapl]
    assert routed.company["NVDA"] == [a_nvda]
    assert routed.macro == []


# ---------------------------------------------------------------------------
# (c) Matching behaviour knobs
# ---------------------------------------------------------------------------

def test_threshold_is_respected():
    """Raising the threshold above the mention count disables roundup routing."""
    art = _article("AAPL", "Apple, Microsoft and Nvidia rally")

    routed = _route([art], roundup_threshold=4)

    # Three mentions < threshold 4 → not a roundup; AAPL is mentioned, so it
    # stays company-specific.
    assert routed.company["AAPL"] == [art]
    assert routed.macro == []


def test_symbol_only_matching_without_company_names():
    """Without ``company_names``, prose names cannot match — documents the degraded mode."""
    art = _article("AAPL", "Apple, Microsoft and Nvidia rally")

    routed = _route([art], company_names=None)

    # Symbols ("aapl", "msft", "nvda") never appear in the prose headline, so
    # nothing matches: not a roundup, not AAPL-specific → macro, untagged.
    # Callers are responsible for passing names (the fetch agent does).
    assert routed.company["AAPL"] == []
    assert len(routed.macro) == 1
    assert routed.macro[0].mentioned_tickers == []


# ---------------------------------------------------------------------------
# (d) Contract shape, determinism, serialisation
# ---------------------------------------------------------------------------

def test_company_dict_has_key_for_every_watchlist_ticker():
    """Even an empty input produces a company key per watchlist ticker (Plan 2 contract)."""
    routed = _route([])

    assert sorted(routed.company.keys()) == sorted(WATCHLIST)
    assert all(v == [] for v in routed.company.values())
    assert routed.macro == []


def test_route_is_deterministic():
    """Identical inputs produce identical outputs — required for backtest reproducibility."""
    arts = [
        _article("NVDA", "Nvidia, Tesla and Apple are today's big movers"),
        _article("AAPL", "Apple unveils new iPhone"),
        _article("MSFT", "Risk is back on as stocks rally"),
    ]

    first  = _route(arts).model_dump()
    second = _route(arts).model_dump()

    assert first == second


def test_macro_article_serialises_datetimes_to_iso():
    """``model_dump(mode='json')`` yields ISO strings — the ADK-state contract."""
    art   = _article("NVDA", "Nvidia, Tesla and Apple are today's big movers")
    macro = _route([art]).macro[0]

    payload = macro.model_dump(mode="json")

    assert isinstance(payload["article"]["published_at"], str)
    # Round-trips as an ISO timestamp.
    assert datetime.fromisoformat(payload["article"]["published_at"])
    assert payload["mentioned_tickers"] == ["AAPL", "NVDA", "TSLA"]


# ---------------------------------------------------------------------------
# (e) Loud failures
# ---------------------------------------------------------------------------

def test_empty_watchlist_raises():
    """An empty watchlist is a wiring bug, not a quiet no-op."""
    with pytest.raises(ValueError, match="watchlist"):
        route_articles([_article("AAPL", "Apple beats")], [])


def test_low_threshold_raises():
    """A threshold below 2 would classify single-company news as roundups."""
    with pytest.raises(ValueError, match="roundup_threshold"):
        route_articles([], WATCHLIST, roundup_threshold=1)


def test_unknown_feed_ticker_raises():
    """An article fetched under a non-watchlist feed signals a wiring bug."""
    art = _article("GOOG", "Alphabet shakes up search")

    with pytest.raises(ValueError, match="GOOG"):
        route_articles([art], WATCHLIST)


# ---------------------------------------------------------------------------
# (f) Moved helpers behave as before (they back fetch.py's aliases in Task 2)
# ---------------------------------------------------------------------------

def test_build_company_terms_expansion():
    """Symbol, full name, and first word of a multi-word name are all terms."""
    assert build_company_terms("Lockheed Martin", "LMT") == ["lmt", "lockheed martin", "lockheed"]
    assert build_company_terms(None, "AAPL") == ["aapl"]


def test_count_roundup_companies_counts_distinct():
    """Each watchlist company counts at most once regardless of term repeats."""
    universe = [{"symbol": s, "name": NAMES[s]} for s in WATCHLIST]

    text = "apple and aapl and microsoft moved today"

    assert count_roundup_companies(text, universe) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_router.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'agents.analysts.news.router'`.

- [ ] **Step 3: Write the router module**

Create `src/agents/analysts/news/router.py` with exactly this content. `build_company_terms` and `count_roundup_companies` are the bodies of `fetch.py`'s `_build_company_terms` / `_count_roundup_companies` moved here verbatim (Task 2 deletes the originals and aliases them back):

```python
"""Specificity router — splits pooled watchlist news into company and macro streams.

Phase 14 Plan 3.  The roundup-demotion logic introduced in ``news/fetch.py``
(commit ``a46f14e``) classified multi-company roundup headlines so they could
be *binned* (demoted to specificity score 0).  This module inverts that
decision from "bin" to "route": the same deterministic classification now
decides which of two streams a unique article feeds —

  * **company** — articles specific to the watchlist ticker whose feed
    carried them; consumed by the per-ticker News branch (Plan 2).
  * **macro**   — multi-company roundups, market summaries, and generic
    articles specific to none of their feeds; consumed by the linkage
    analyst (Plan 4) via the ``macro_articles`` session-state key.

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

    # Every watchlist ticker gets a company key up front (Plan 2 contract).
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_router.py -v`
Expected: all 15 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/router.py tests/unit/agents/analysts/news/test_router.py
git commit -m "feat(news): add pure specificity router — company vs macro stream (Phase 14 Plan 3)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Thin delegation — fetch.py aliases the moved classification helpers

**Files:**
- Modify: `src/agents/analysts/news/fetch.py:168-243` (delete the two moved functions; add alias imports)
- Test: existing `tests/unit/agents/analysts/news/test_fetch.py` (unchanged — the aliases keep its imports working), plus one new alias-identity test appended to `tests/unit/agents/analysts/news/test_router.py`

**Interfaces:**
- Consumes: `router.build_company_terms`, `router.count_roundup_companies` (Task 1).
- Produces: `fetch._build_company_terms` and `fetch._count_roundup_companies` remain importable under their historical names (existing tests and `_score_article_specificity` depend on them). `_score_article_specificity` and everything else in `fetch.py` is otherwise untouched — Plan 2 owns the restructure.

- [ ] **Step 1: Write the failing alias-identity test**

Append to `tests/unit/agents/analysts/news/test_router.py`:

```python
# ---------------------------------------------------------------------------
# (g) fetch.py delegation — historical names must be the router's objects
# ---------------------------------------------------------------------------

def test_fetch_aliases_delegate_to_router():
    """fetch.py's historical private names must BE the router functions.

    Guards against the two implementations drifting apart — there must be
    exactly one copy of the classification logic (spec §6.2: the router owns
    it; fetch.py merely delegates until Plan 2's rebuild).
    """
    from agents.analysts.news import fetch as fetch_mod
    from agents.analysts.news import router as router_mod

    assert fetch_mod._build_company_terms is router_mod.build_company_terms
    assert fetch_mod._count_roundup_companies is router_mod.count_roundup_companies
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_router.py::test_fetch_aliases_delegate_to_router -v`
Expected: FAIL with `AssertionError` (fetch.py still has its own local definitions).

- [ ] **Step 3: Replace the two function bodies in fetch.py with alias imports**

In `src/agents/analysts/news/fetch.py`:

(a) Delete the entire definitions of `_build_company_terms` (currently lines 168–208, signature `def _build_company_terms(company_name: str | None, symbol: str) -> list[str]:`) and `_count_roundup_companies` (currently lines 211–243, signature `def _count_roundup_companies(text: str, watchlist_universe: list[dict[str, str]]) -> int:`). Delete both docstrings and bodies completely — nothing else.

(b) In the import block at the top of the module, immediately after the line `from config.analysts import NewsCaps, get_analysts_config`, add:

```python
# Classification helpers moved to ``agents.analysts.news.router`` (Phase 14
# Plan 3) where they back the company/macro stream router.  Re-exported here
# under their historical private names so ``_score_article_specificity`` and
# the existing test suite keep working unchanged — Plan 2's rebuild owns any
# further restructure of this module.
from agents.analysts.news.router import (
    build_company_terms as _build_company_terms,
    count_roundup_companies as _count_roundup_companies,
)
```

`_score_article_specificity` is NOT modified — its calls to `_count_roundup_companies` / `_build_company_terms` now resolve to the router's functions through the aliases.

- [ ] **Step 4: Run the full news test package to verify nothing regressed**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/ -v`
Expected: all tests PASS, including every pre-existing test in `test_fetch.py` (which imports `_count_roundup_companies` and `_score_article_specificity` from `agents.analysts.news.fetch`) and the new alias-identity test.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/news/fetch.py tests/unit/agents/analysts/news/test_router.py
git commit -m "refactor(news): fetch.py delegates classification helpers to the router

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: NewsFetchAgent emits `state["macro_articles"]`

**Files:**
- Modify: `src/agents/analysts/news/fetch_agent.py`
- Test: `tests/unit/agents/analysts/news/test_fetch_agent.py` (fixture modernisation + two new tests)

**Interfaces:**
- Consumes: `router.route_articles` / `router.MacroArticle` (Task 1); `config.analysts.get_analysts_config().news.roundup_company_threshold` (existing config); `orchestrator.stock_picker.get_watchlist_with_names` (existing).
- Produces (pinned, consumed by Plan 4): `state["macro_articles"]` — a list of `MacroArticle.model_dump(mode="json")` dicts, shape `{"article": {<serialised NewsArticle, published_at as ISO string>}, "mentioned_tickers": ["..."]}`. The key is **always** written (empty list on a quiet tick) so Plan 4 can distinguish "no macro news" from "stage never ran". It is deliberately NOT `temp:`-prefixed — ADK strips `temp:` keys at the invocation boundary and Plan 4's linkage branch reads this key; `mode="json"` ISO-stringification is mandatory because the backtest `DatabaseSessionService` cannot hold `datetime`.

- [ ] **Step 1: Modernise the existing mock fixtures to the provider contract**

The three existing tests mock `get_stock_news` with bare dicts (`{"title": ..., "summary": ..., "published_at": ...}`) that are not valid `NewsArticle` payloads. The real provider contract is `list[NewsArticle]`; the routing step added below validates dicts loudly, so the fixtures must become `NewsArticle`-valid. Keep the extra `"title"` key (Pydantic ignores extras; the existing assertions on `["title"]` and the renderer's dual-key lookup keep passing).

In `tests/unit/agents/analysts/news/test_fetch_agent.py`:

Replace (in `test_fetch_writes_per_ticker_context_keys`):

```python
    fake_news = {
        "AAPL": [{"title": "AAPL beats", "summary": "Strong quarter.", "published_at": "2026-05-21"}],
        "MSFT": [{"title": "MSFT guides up", "summary": "Cloud strong.", "published_at": "2026-05-21"}],
    }
```

with:

```python
    # NewsArticle-valid dicts (ticker/headline/url are required by the model);
    # the redundant "title" key is kept because the renderer and the
    # assertions below use dual-key lookup.
    fake_news = {
        "AAPL": [{
            "ticker": "AAPL", "title": "AAPL beats", "headline": "AAPL beats",
            "summary": "Strong quarter.", "url": "https://example.com/aapl-1",
            "published_at": "2026-05-21",
        }],
        "MSFT": [{
            "ticker": "MSFT", "title": "MSFT guides up", "headline": "MSFT guides up",
            "summary": "Cloud strong.", "url": "https://example.com/msft-1",
            "published_at": "2026-05-21",
        }],
    }
```

Replace (in `test_fetch_degrades_on_provider_error`):

```python
        return [{"title": "AAPL beats", "summary": "ok.", "published_at": "2026-05-21"}]
```

with:

```python
        return [{
            "ticker": "AAPL", "title": "AAPL beats", "headline": "AAPL beats",
            "summary": "ok.", "url": "https://example.com/aapl-1",
            "published_at": "2026-05-21",
        }]
```

Replace (in `test_fetch_writes_aggregate_news_context_for_trace`):

```python
    async def _mock(ticker, as_of=None):
        return [{"title": f"{ticker} hed", "summary": "body", "published_at": "2026-05-21"}]
```

with:

```python
    async def _mock(ticker, as_of=None):
        return [{
            "ticker": ticker, "title": f"{ticker} hed", "headline": f"{ticker} hed",
            "summary": "body", "url": f"https://example.com/{ticker.lower()}-1",
            "published_at": "2026-05-21",
        }]
```

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_fetch_agent.py -v`
Expected: all 3 existing tests still PASS (the current agent passes dicts straight through, so richer dicts change nothing yet).

- [ ] **Step 2: Write the failing macro-emission tests**

Append to `tests/unit/agents/analysts/news/test_fetch_agent.py`:

```python
# ---------------------------------------------------------------------------
# macro_articles emission (Phase 14 Plan 3)
# ---------------------------------------------------------------------------

from types import SimpleNamespace


def _fixture_universe():
    """Deterministic watchlist universe for macro-routing tests.

    Returns
    -------
    list[dict[str, str]]
        ``{"symbol", "name"}`` entries mirroring get_watchlist_with_names.
    """
    return [
        {"symbol": "AAPL", "name": "Apple"},
        {"symbol": "MSFT", "name": "Microsoft"},
        {"symbol": "NVDA", "name": "Nvidia"},
    ]


def _fixture_analysts_config():
    """Minimal stand-in for get_analysts_config() exposing the roundup knob.

    Returns
    -------
    SimpleNamespace
        Object with ``.news.roundup_company_threshold`` set to 3.
    """
    return SimpleNamespace(news=SimpleNamespace(roundup_company_threshold=3))


@pytest.mark.asyncio
async def test_macro_articles_key_emitted_with_roundup():
    """A roundup ARRIVES in state['macro_articles'], tagged and ISO-serialised."""

    roundup = {
        "ticker": "AAPL",
        "headline": "Apple, Microsoft and Nvidia lead a broad market rally",
        "summary": "Megacaps drove the index higher.",
        "url": "https://example.com/roundup-1",
        "published_at": "2026-05-21T14:00:00",
    }
    specific = {
        "ticker": "AAPL",
        "headline": "Apple ships new iPhone",
        "summary": "Launch day.",
        "url": "https://example.com/aapl-2",
        "published_at": "2026-05-21T15:00:00",
    }

    async def _mock(ticker, as_of=None):
        return [roundup, specific] if ticker == "AAPL" else []

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT", "NVDA"], "as_of": datetime(2026, 5, 21, 16, 0)},
        session_id="t1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(session_service=svc, session=session,
                            invocation_id="inv-1", agent=agent)

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock), \
         patch("agents.analysts.news.fetch_agent.get_watchlist_with_names",
               _fixture_universe), \
         patch("agents.analysts.news.fetch_agent.get_analysts_config",
               _fixture_analysts_config):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta

    # Key is present, NOT temp:-prefixed, and carries exactly the roundup.
    assert "macro_articles" in sd
    macro = sd["macro_articles"]
    assert len(macro) == 1
    assert macro[0]["article"]["headline"] == roundup["headline"]
    assert macro[0]["mentioned_tickers"] == ["AAPL", "MSFT", "NVDA"]

    # ISO-stringified datetimes — the DatabaseSessionService contract.
    assert isinstance(macro[0]["article"]["published_at"], str)

    # The company-specific article did not leak into the macro stream.
    assert all(m["article"]["url"] != specific["url"] for m in macro)


@pytest.mark.asyncio
async def test_macro_articles_key_present_when_empty():
    """A quiet tick still writes macro_articles == [] (key presence contract)."""

    async def _mock(ticker, as_of=None):
        return [{
            "ticker": ticker, "headline": f"{ticker} quarterly note",
            "summary": "", "url": f"https://example.com/{ticker.lower()}-q",
            "published_at": "2026-05-21T14:00:00",
        }] if ticker == "AAPL" else []

    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test", user_id="test",
        state={"tickers": ["AAPL", "MSFT", "NVDA"], "as_of": datetime(2026, 5, 21)},
        session_id="t1",
    )
    agent = NewsFetchAgent(name="NewsFetch")
    ctx = InvocationContext(session_service=svc, session=session,
                            invocation_id="inv-1", agent=agent)

    with patch("agents.analysts.news.fetch_agent.get_stock_news", _mock), \
         patch("agents.analysts.news.fetch_agent.get_watchlist_with_names",
               _fixture_universe), \
         patch("agents.analysts.news.fetch_agent.get_analysts_config",
               _fixture_analysts_config):
        events = [ev async for ev in agent.run_async(ctx)]

    sd = events[0].actions.state_delta

    assert sd["macro_articles"] == []
```

Note: `"AAPL quarterly note"` does not contain "aapl"/"apple"? It contains the ticker text "AAPL" — lower-cased to "aapl", which the symbol term matches, so it routes to `company["AAPL"]`, leaving macro empty. That is the point of the second test.

- [ ] **Step 3: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/test_fetch_agent.py -v`
Expected: the two new tests FAIL with `KeyError: 'macro_articles'` (or an assertion on key presence); the three modernised tests still PASS.

- [ ] **Step 4: Implement the emission in fetch_agent.py**

Three edits to `src/agents/analysts/news/fetch_agent.py`:

(a) Extend the import block. After the existing line `from agents.analysts.news.fetch import _build_ticker_news_context`, add:

```python
from agents.analysts.news.router import route_articles
from config.analysts import get_analysts_config
from data.models import NewsArticle
```

(b) Inside `_run_async_impl`, replace:

```python
        news_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []
```

with:

```python
        news_data: dict[str, dict] = {}
        per_ticker_blocks: dict[str, str] = {}

        # Pooled model-typed articles across every feed — input to the
        # company/macro specificity router (Phase 14 Plan 3).
        pooled: list[NewsArticle] = []

        for ticker in tickers:
            try:
                articles = await get_stock_news(ticker, as_of=as_of)
            except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
                _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
                articles = []

            # Coerce to NewsArticle for the router pool.  The provider
            # contract is list[NewsArticle]; dict payloads (cache layers,
            # test doubles) are validated LOUDLY — a malformed article is a
            # contract violation and must raise, never silently vanish from
            # the macro stream (spec §7).
            for raw in articles:
                pooled.append(
                    raw if isinstance(raw, NewsArticle)
                    else NewsArticle.model_validate(raw)
                )
```

(c) After the aggregate-context block (the statement assigning `delta["temp:news_context"] = ...`) and before the `trace_maybe(...)` call, insert:

```python
        # ── Macro-stream routing (Phase 14 Plan 3) ─────────────────────────
        # Split the pooled feeds into company-specific vs macro (roundup /
        # market-summary) streams.  Only the macro side is emitted here —
        # Plan 4's linkage branch consumes it; Plan 2's rebuilt fetch flow
        # takes over the .company side.  The key is written on every tick
        # (empty list on a quiet tick) so downstream can distinguish "no
        # macro news" from "stage never ran".  It is deliberately NOT
        # temp:-prefixed, and model_dump(mode="json") ISO-stringifies the
        # datetimes — the backtest DatabaseSessionService cannot hold
        # datetime objects.
        if tickers:
            routed = route_articles(
                pooled,
                tickers,
                company_names=watchlist_names,
                roundup_threshold=(
                    get_analysts_config().news.roundup_company_threshold
                ),
            )
            delta["macro_articles"] = [
                ma.model_dump(mode="json") for ma in routed.macro
            ]
        else:
            # Degenerate tick with no tickers — nothing to route, but the
            # key contract still holds.
            delta["macro_articles"] = []
```

(d) Update the module docstring's "Yielded keys" list — add one line after the `temp:news_context` bullet:

```python
  - ``macro_articles`` — serialised MacroArticle dicts (roundups / market
    summaries routed off the per-ticker streams; Phase 14 Plan 3).  NOT
    temp:-prefixed — persisted for the linkage branch (Plan 4).
```

- [ ] **Step 5: Run the full news test package to verify everything passes**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/news/ -v`
Expected: all tests PASS (router, fetch, dedup/recency, fetch_agent including the two new macro tests, joiner, prompts).

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/news/fetch_agent.py tests/unit/agents/analysts/news/test_fetch_agent.py
git commit -m "feat(news): NewsFetchAgent emits macro_articles session-state key

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Config-driven news article caps — live/backtest parity

**Files:**
- Modify: `config/data.json`
- Modify: `src/data/config.py:22-43` (`FetchDefaults`)
- Modify: `src/data/__init__.py:155-196` (`get_stock_news`)
- Modify: `src/backtest/providers/news_cache.py`
- Modify: `scripts/backtest_fetch.py:261-292` (`_news`)
- Modify: `config/README.md` (data.json table)
- Test: `tests/unit/data/test_news_limits.py` (new — remember `git add -f`)

**Interfaces:**
- Consumes: `data.config.get_config()` (existing loader); `backtest.providers._store_handle.get_store` (existing).
- Produces:
  - `FetchDefaults.news_per_tick_limit: int` (default **400**) — the per-tick newest-N cap applied identically by `data.get_stock_news` (when the caller passes no `limit`) and by the backtest cache news provider. Sized from the baseline-2025-09 cache: busiest observed 7-day per-ticker volume was 318 (NVDA), so 400 guarantees roundups are never truncated out before the router runs, live or replayed.
  - `FetchDefaults.news_backfill_limit: int` (default **9000**) — the cache-fill cap, replacing the hardcoded `limit=9000` in `scripts/backtest_fetch.py`.
  - `get_stock_news(..., limit: int | None = None, ...)` — `None` now means "resolve from config", not "uncapped". (Call-site survey 2026-07-06: only `fetch_agent.py` calls without `limit` and only `backtest_fetch.py` passes one explicitly, so no caller loses an uncapped path.)
  - Cache news provider `fetch(..., limit: int | None = None, ...)` — honours the dispatcher-resolved `limit` with a newest-N head slice (its `read_news` result is already most-recent-first), closing the live/backtest parity gap (D2).

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/data/test_news_limits.py`:

```python
"""Unit tests for the config-driven news article caps (Phase 14 Plan 3).

Two caps, one parity rule:

  * ``news_per_tick_limit``  — resolved by ``data.get_stock_news`` when the
    caller passes no explicit ``limit`` AND honoured by the backtest cache
    provider, so live and replay see the identical newest-N list (spec D2).
  * ``news_backfill_limit``  — the cache-fill cap consumed by
    ``scripts.backtest_fetch`` (replaces the old hardcoded 9000).
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from data.models import NewsArticle


def test_fetch_defaults_expose_news_limits():
    """Both caps exist on FetchDefaults with the documented defaults."""
    from data.config import FetchDefaults

    defaults = FetchDefaults()

    assert defaults.news_per_tick_limit == 400
    assert defaults.news_backfill_limit == 9000


def test_config_json_carries_news_limits():
    """config/data.json declares both caps (config-file convention)."""
    from pathlib import Path

    from data.config import load_config_from

    cfg = load_config_from(Path("config/data.json"))

    assert cfg.defaults.news_per_tick_limit == 400
    assert cfg.defaults.news_backfill_limit == 9000


@pytest.mark.asyncio
async def test_dispatcher_resolves_per_tick_limit_from_config(monkeypatch):
    """get_stock_news with no explicit limit forwards the config value."""
    import data as data_pkg
    from data.config import get_config

    captured: dict = {}

    async def _fake_dispatch(domain, ticker, **kwargs):
        """Capture the kwargs the dispatcher would forward to the provider."""
        captured.update(kwargs)
        return []

    monkeypatch.setattr(data_pkg, "_dispatch", _fake_dispatch)

    await data_pkg.get_stock_news("AAPL", as_of=datetime(2026, 2, 10, tzinfo=UTC))

    assert captured["limit"] == get_config().defaults.news_per_tick_limit


@pytest.mark.asyncio
async def test_dispatcher_honours_explicit_limit(monkeypatch):
    """An explicit caller limit (e.g. the backfill) is forwarded untouched."""
    import data as data_pkg

    captured: dict = {}

    async def _fake_dispatch(domain, ticker, **kwargs):
        """Capture the forwarded kwargs."""
        captured.update(kwargs)
        return []

    monkeypatch.setattr(data_pkg, "_dispatch", _fake_dispatch)

    await data_pkg.get_stock_news(
        "AAPL", as_of=datetime(2026, 2, 10, tzinfo=UTC), limit=7,
    )

    assert captured["limit"] == 7


@pytest.mark.asyncio
async def test_cache_provider_applies_limit(monkeypatch):
    """The cache news provider head-slices to the dispatcher-resolved limit."""
    from backtest.providers import news_cache

    base = datetime(2026, 2, 10, 12, 0)

    # Five articles, newest first — matching read_news's descending order.
    articles = [
        NewsArticle(
            ticker="AAPL",
            headline=f"story {i}",
            url=f"https://example.com/{i}",
            published_at=base - timedelta(hours=i),
        )
        for i in range(5)
    ]

    class _Store:
        """Stub store returning the fixed descending article list."""

        def read_news(self, ticker, as_of, lookback_days):
            """Mimic CachedDataStore.read_news (most-recent first)."""
            return articles

    monkeypatch.setattr(news_cache, "get_store", lambda: _Store())

    out = await news_cache.fetch(
        "AAPL", as_of=base, lookback_days=7, limit=2,
    )

    # Newest two survive — identical to the live provider's sort-then-cap.
    assert out == articles[:2]


@pytest.mark.asyncio
async def test_cache_provider_unlimited_when_limit_none(monkeypatch):
    """limit=None keeps the whole lookback window (fill-time behaviour)."""
    from backtest.providers import news_cache

    base = datetime(2026, 2, 10, 12, 0)

    articles = [
        NewsArticle(
            ticker="AAPL",
            headline=f"story {i}",
            url=f"https://example.com/{i}",
            published_at=base - timedelta(hours=i),
        )
        for i in range(3)
    ]

    class _Store:
        """Stub store returning the fixed article list."""

        def read_news(self, ticker, as_of, lookback_days):
            """Mimic CachedDataStore.read_news."""
            return articles

    monkeypatch.setattr(news_cache, "get_store", lambda: _Store())

    out = await news_cache.fetch("AAPL", as_of=base, lookback_days=7, limit=None)

    assert out == articles
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/data/test_news_limits.py -v`
Expected: `test_fetch_defaults_expose_news_limits` and `test_config_json_carries_news_limits` FAIL with `AttributeError`/`AssertionError` (fields missing); `test_dispatcher_resolves_per_tick_limit_from_config` FAILS (captured limit is the old hardcoded 50); `test_cache_provider_applies_limit` FAILS (limit swallowed by `**_unused`, all 5 returned).

- [ ] **Step 3: Add the two fields to FetchDefaults**

In `src/data/config.py`, inside `class FetchDefaults`, after the `filings_8k_staleness_days` field, add:

```python
    # Per-tick article cap for news reads — applied identically by the live
    # dispatcher default (``data.get_stock_news``) and the backtest cache
    # news provider, so both paths see the same newest-N list (backtest/live
    # parity, Phase 14 spec D2).  Sized from the baseline-2025-09 cache: the
    # busiest observed 7-day per-ticker volume was 318 articles (NVDA), so
    # 400 leaves headroom and roundup articles are never truncated out
    # before the specificity router runs.  The old hardcoded dispatcher
    # default of 50 silently dropped ~85 % of a megacap's weekly feed.
    news_per_tick_limit:          int  = 400

    # Article cap for backtest cache-fill news pulls (scripts/backtest_fetch).
    # Sized to absorb a full multi-week window for the noisiest names while
    # still bounding memory in pathological cases (was hardcoded 9000 in the
    # fill script; moved here per the config convention).
    news_backfill_limit:          int  = 9000
```

- [ ] **Step 4: Add the values to config/data.json**

In `config/data.json`, inside `"defaults"`, after `"form4_max_filings": 1000`, add (keeping valid JSON — mind the commas):

```json
    "news_per_tick_limit": 400,
    "news_backfill_limit": 9000
```

- [ ] **Step 5: Resolve the per-tick limit in the dispatcher**

In `src/data/__init__.py`, `get_stock_news`:

(a) Change the signature line `limit: int | None = 50,` to `limit: int | None = None,`.

(b) Update the `limit:` docstring entry to:

```python
    limit:
        Maximum number of articles to return.  ``None`` (the default)
        resolves to ``defaults.news_per_tick_limit`` from
        ``config/data.json`` so live ticks and backtest cache reads share
        one cap (parity).  Callers with special needs (e.g. the backtest
        cache-fill) pass an explicit value.
```

(c) Replace the body's dispatch tail:

```python
    lookback_days = get_config().defaults.news_lookback_days
    return await _dispatch(
        "news",
        ticker.upper(),
        from_date=from_date or (as_of_date - _td(days=lookback_days)),
        to_date=to_date or as_of_date,
        limit=limit,
        lookback_days=lookback_days,
        as_of=as_of,
    )
```

with:

```python
    lookback_days = get_config().defaults.news_lookback_days

    # Per-tick cap: config-sourced when the caller does not specify one, so
    # the live Finnhub provider and the backtest cache provider apply the
    # identical newest-N cut (Phase 14 spec D2 — parity by construction).
    resolved_limit = (
        limit if limit is not None
        else get_config().defaults.news_per_tick_limit
    )

    return await _dispatch(
        "news",
        ticker.upper(),
        from_date=from_date or (as_of_date - _td(days=lookback_days)),
        to_date=to_date or as_of_date,
        limit=resolved_limit,
        lookback_days=lookback_days,
        as_of=as_of,
    )
```

- [ ] **Step 6: Honour the limit in the cache news provider**

In `src/backtest/providers/news_cache.py`, replace the `fetch` function with:

```python
@register("news", "cache", upstream="cache", rate_per_minute=1_000_000, burst=1_000)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int,   # required — defaults now flow from get_config() in caller
    limit: int | None = None,
    **_unused,
) -> list[NewsArticle]:
    """Return news articles for ``ticker`` published at or before ``as_of``.

    The PIT filter is applied by the store: articles whose ``published_at``
    exceeds ``as_of`` are never returned, preventing lookahead bias.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``published_at``.
    lookback_days:
        How many calendar days before ``as_of`` to include.  Required — the
        caller is responsible for supplying the value from ``get_config()``.
    limit:
        Maximum number of articles to return, newest first.  Forwarded by
        the dispatcher (``data.get_stock_news``) from
        ``defaults.news_per_tick_limit`` so a backtest tick sees the same
        newest-N cut a live tick would (Phase 14 spec D2 — parity).
        ``None`` keeps the whole lookback window.

    Returns
    -------
    list[NewsArticle]
        Matching articles, most-recent first, capped at ``limit``.
    """
    articles = get_store().read_news(ticker, as_of=as_of, lookback_days=lookback_days)

    # ``read_news`` returns most-recent-first, so a head slice keeps the
    # newest N — exactly the articles the live Finnhub provider (which sorts
    # newest-first before capping) would return under the same ``limit``.
    if limit is not None:
        articles = articles[:limit]

    return articles
```

- [ ] **Step 7: Source the backfill limit from config in the fill script**

In `scripts/backtest_fetch.py`, `_news`:

(a) Replace the final paragraph of the docstring (the one beginning ```` ``limit`` is set explicitly to ``2000`` ````, which stale-claims 2000) with:

```python
        ``limit`` is set explicitly to ``defaults.news_backfill_limit``
        (from ``config/data.json``) rather than relying on the dispatcher's
        per-tick default — the per-tick cap is sized for live ticks, while
        at cache-fill time we want to preserve the full chunked Finnhub
        pull across the whole window (a high-volume ticker's earliest weeks
        would otherwise be discarded by the newest-first slice).  Per-tick
        reads still serve their capped slice from the cache.
```

(b) Replace the call body:

```python
        from data.config import get_config
        pre_window_buffer = timedelta(days=get_config().defaults.news_lookback_days)
        return await get_stock_news(
            ticker,
            from_date=start - pre_window_buffer,
            to_date=end,
            as_of=_as_of_close(end),
            limit=9000,
        )
```

with:

```python
        from data.config import get_config

        defaults          = get_config().defaults
        pre_window_buffer = timedelta(days=defaults.news_lookback_days)

        return await get_stock_news(
            ticker,
            from_date=start - pre_window_buffer,
            to_date=end,
            as_of=_as_of_close(end),
            limit=defaults.news_backfill_limit,
        )
```

- [ ] **Step 8: Verify no other call site relied on the old defaults**

Run: `grep -rn "get_stock_news(" src/ scripts/ --include=*.py | grep -v "def get_stock_news"`
Expected: exactly two call sites — `src/agents/analysts/news/fetch_agent.py` (no `limit` → config-resolved) and `scripts/backtest_fetch.py` (explicit `defaults.news_backfill_limit`). If any other call site appears, stop and inspect it before proceeding.

- [ ] **Step 9: Document both settings in config/README.md**

In `config/README.md`, in the `data.json` defaults table, after the `defaults.form4_max_filings` row, add:

```markdown
| `defaults.news_per_tick_limit` | int | Per-tick cap on news articles returned to the pipeline, newest first. Applied identically by the live dispatcher (`data.get_stock_news` when the caller passes no `limit`) **and** the backtest cache news provider, so live and replay see the same newest-N list — backtest/live parity (Phase 14 spec D2). Sized from the baseline-2025-09 cache: busiest observed 7-day per-ticker volume was 318 (NVDA); 400 leaves headroom so roundup articles are never truncated out before the specificity router runs. Replaces the old hardcoded dispatcher default of 50. Default 400. |
| `defaults.news_backfill_limit` | int | Cap on news articles per (ticker, window) at backtest cache-fill time (`scripts/backtest_fetch.py`). Sized to absorb a full multi-week window for the noisiest names while bounding memory in pathological cases; per-tick reads still serve their capped slice from the cache. Replaces the old hardcoded `9000` in the fill script. Default 9000. |
```

- [ ] **Step 10: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/data/test_news_limits.py -v`
Expected: all 6 tests PASS.

Then run the broader affected suites to catch regressions:

Run: `.venv/bin/python -m pytest tests/unit/data/ tests/unit/agents/analysts/news/ -v`
Expected: all PASS.

- [ ] **Step 11: Commit (note the forced add for tests/unit/data/)**

```bash
git add config/data.json config/README.md src/data/config.py src/data/__init__.py src/backtest/providers/news_cache.py scripts/backtest_fetch.py
git add -f tests/unit/data/test_news_limits.py
git commit -m "feat(news): config-driven per-tick + backfill article caps with cache parity

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Golden-cache news refetch for the target windows + positive-signal verification

**Files:**
- Create: `docs/Phase14-analyst-refactor/plan3-news-refetch-results.md` (results record — topic-keyed filename, per docs convention)
- No source changes — this task runs `scripts/backtest_fetch.py` (Task 4's version) and verifies the routed macro stream against the refetched caches.

**Interfaces:**
- Consumes: `scripts.backtest_fetch --refetch-domain news` (existing CLI; the fetcher clears each ticker's stale news rows before re-writing, so the refetched state is exactly one fresh pull at `defaults.news_backfill_limit`); `route_articles` (Task 1).
- Produces: refetched `backtests/iran-conflict-2026-02/store.sqlite` and `backtests/baseline-2025-09/store.sqlite` news tables containing the roundup articles Plan 4's eval windows need, plus a written before/after record.

**Operator notes (read before Step 2):**
- The refetch **replaces** each ticker's cached news rows (the fetcher deletes stale rows for a refetched domain before re-writing). House rule: destructive operations need the user's explicit go-ahead — show the exact commands from Step 2 and wait for "go" before running them.
- Requires `FINNHUB_API_KEY` in the environment and ~10–20 minutes per window (rate-limited at 50 calls/min shared across chunked pulls).
- Finnhub `/company-news` retains roughly one year. `baseline-2025-09` sits near that margin from a mid-2026 vantage — the spec (§9) accepts possible thinning, but it must be **visible**: Step 1 records before-counts and Step 4 compares.
- `long-baseline-2025` is deliberately excluded here: its cache is pending the separate revenue-concept refetch (see MEMORY), and Plan 1 owns that window's refresh sequencing.

- [ ] **Step 1: Record before-counts for both windows**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
"""Record pre-refetch news-table shape for the Plan 3 target windows."""
import sqlite3

for window in ("iran-conflict-2026-02", "baseline-2025-09"):
    db = sqlite3.connect(f"backtests/{window}/store.sqlite")

    total, tickers = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker) FROM news_articles"
    ).fetchone()

    # Cross-ticker shared URLs are the deterministic roundup proxy —
    # Finnhub staples one roundup story onto many feeds under one URL.
    shared = db.execute(
        "SELECT COUNT(*) FROM (SELECT url FROM news_articles "
        "GROUP BY url HAVING COUNT(DISTINCT ticker) >= 2)"
    ).fetchone()[0]

    print(f"BEFORE {window}: rows={total} tickers={tickers} cross_ticker_urls={shared}")

    for t, n in db.execute(
        "SELECT ticker, COUNT(*) FROM news_articles GROUP BY ticker ORDER BY ticker"
    ):
        print(f"  {t}: {n}")
EOF
```

Expected: per-window totals print without error. Save the output — Step 5 records it.

- [ ] **Step 2: Refetch the news domain for both windows (after explicit user go-ahead)**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window iran-conflict-2026-02 --refetch-domain news
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window baseline-2025-09 --refetch-domain news
```

Expected: per-ticker `refetch news/<TICKER> — cleared N stale row(s) before re-write` log lines followed by successful fill lines; the run ends with `Cache fill complete.` and no `status='error'` rows for the news domain. If Finnhub truncation warnings (`>= truncation threshold 240`) appear for specific chunks, note them in the results file — they flag possible partial weeks, not failures.

- [ ] **Step 3: Verify roundups are present (positive signal, not absence of error)**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
"""Post-refetch check: roundup articles must be PRESENT in both caches."""
import sqlite3

for window in ("iran-conflict-2026-02", "baseline-2025-09"):
    db = sqlite3.connect(f"backtests/{window}/store.sqlite")

    total, tickers = db.execute(
        "SELECT COUNT(*), COUNT(DISTINCT ticker) FROM news_articles"
    ).fetchone()

    shared = db.execute(
        "SELECT COUNT(*) FROM (SELECT url FROM news_articles "
        "GROUP BY url HAVING COUNT(DISTINCT ticker) >= 2)"
    ).fetchone()[0]

    print(f"AFTER {window}: rows={total} tickers={tickers} cross_ticker_urls={shared}")

    # Loud assertion: a refetched window with zero cross-ticker URLs means
    # the roundups the macro stream depends on are absent — fail, don't shrug.
    assert shared > 0, f"{window}: no cross-ticker URLs — roundups absent, refetch failed"
EOF
```

Expected: both windows print non-zero `cross_ticker_urls` and the assertions pass. Compare per-window `rows` against Step 1: `iran-conflict-2026-02` should hold steady or grow; if `baseline-2025-09` shrank materially (> 20 % on any ticker), record the thinning per the retention caveat — it bounds Plan 2/4 eval power on that window but does not block this plan.

- [ ] **Step 4: Router smoke over a refetched cache — the macro stream must be non-empty**

```bash
PYTHONPATH=src .venv/bin/python - <<'EOF'
"""Route the entire refetched iran-conflict-2026-02 news table and assert a
non-empty macro stream — the end-to-end positive-signal check for Plan 3."""
import sqlite3
from datetime import datetime

from agents.analysts.news.router import route_articles
from config.analysts import get_analysts_config
from data.models import NewsArticle
from orchestrator.stock_picker import get_watchlist_with_names

universe  = get_watchlist_with_names()
watchlist = [e["symbol"] for e in universe]
names     = {e["symbol"]: e["name"] for e in universe}
threshold = get_analysts_config().news.roundup_company_threshold

db   = sqlite3.connect("backtests/iran-conflict-2026-02/store.sqlite")
rows = db.execute(
    "SELECT ticker, headline, summary, url, source, published_at FROM news_articles"
).fetchall()

# Rebuild NewsArticle models from the raw rows; skip tickers no longer on
# the watchlist (watchlist edits since the fill are legitimate).
pooled = [
    NewsArticle(
        ticker=t, headline=h or "", summary=s or "", url=u,
        source=src or "", published_at=datetime.fromisoformat(p),
    )
    for t, h, s, u, src, p in rows
    if t in set(watchlist)
]

routed    = route_articles(pooled, watchlist, company_names=names, roundup_threshold=threshold)
n_company = sum(len(v) for v in routed.company.values())

print(f"pooled={len(pooled)} company={n_company} macro={len(routed.macro)}")

for m in routed.macro[:5]:
    print(f"  MACRO [{','.join(m.mentioned_tickers) or '-'}] {m.article.headline[:80]}")

assert routed.macro, "no macro articles routed — positive-signal check failed"
assert any(m.mentioned_tickers for m in routed.macro), (
    "no macro article carries ticker tags — roundup classification not firing"
)
EOF
```

Expected: prints pooled/company/macro counts with `macro` well above zero, sample macro headlines with ticker tags, and both assertions pass.

- [ ] **Step 5: Record the results**

Create `docs/Phase14-analyst-refactor/plan3-news-refetch-results.md` containing: the refetch date, the two commands run, the Step 1 before-table and Step 3 after-table (rows / cross-ticker URLs per window), any truncation warnings from Step 2, any `baseline-2025-09` thinning observed (with the retention caveat noted), and the Step 4 smoke output (pooled/company/macro counts plus the sample headlines). Keep it factual — numbers and commands, no narrative.

- [ ] **Step 6: Full-suite regression run**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: entire suite PASSES.

Run: `.venv/bin/python -m ruff check src/ scripts/ tests/`
Expected: no findings.

- [ ] **Step 7: Commit the results record**

```bash
git add docs/Phase14-analyst-refactor/plan3-news-refetch-results.md
git commit -m "docs(backtest): record Plan 3 news refetch results for target windows

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Self-review (completed at plan-writing time)

- **Spec coverage (§6.2):** router extraction with bin→route inversion → Tasks 1–2; one shared `NewsArticle` model, macro as routing destination → Task 1 (`MacroArticle` wraps, never replaces); cache refetch so roundups discarded at the cap margin are present → Tasks 4–5; D2 parity (identical endpoint, identical routing, identical caps) → Task 4; D1/D2 (no new providers, no general-news feed) → nothing in this plan touches providers or adds endpoints; §7 loud failures → router ValueErrors, loud article validation in the fetch agent, refetch assertions; §8 "router determinism on fixture articles" and the positive-signal integration guard → Task 1 tests + Task 5 Steps 3–4. No LLM calls anywhere.
- **Placeholder scan:** every code step contains complete, runnable code; no TBD/TODO/"similar to Task N".
- **Type consistency:** `route_articles(articles: list[NewsArticle], watchlist: list[str], *, company_names: dict[str, str] | None, roundup_threshold: int) -> RoutedArticles` is identical in Task 1's module, Task 3's agent call, and Task 5's smoke; `macro_articles` payload shape (`{"article": {...}, "mentioned_tickers": [...]}`) matches between Task 1's serialisation test and Task 3's agent tests; `limit: int | None = None` semantics match across dispatcher, cache provider, and fill script in Task 4.
