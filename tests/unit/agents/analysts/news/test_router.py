"""Unit tests for the specificity router (Phase 14 Plan 2).

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
    """Even an empty input produces a company key per watchlist ticker (Plan 3 contract)."""
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


# ---------------------------------------------------------------------------
# (g) fetch.py delegation — historical names must be the router's objects
# ---------------------------------------------------------------------------

def test_fetch_aliases_delegate_to_router():
    """fetch.py's historical private names must BE the router functions.

    Guards against the two implementations drifting apart — there must be
    exactly one copy of the classification logic (spec §6.2: the router owns
    it; fetch.py merely delegates until Plan 3's rebuild).
    """
    from agents.analysts.news import fetch as fetch_mod
    from agents.analysts.news import router as router_mod

    assert fetch_mod._build_company_terms is router_mod.build_company_terms
    assert fetch_mod._count_roundup_companies is router_mod.count_roundup_companies
