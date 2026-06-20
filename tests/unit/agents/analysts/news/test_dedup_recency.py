"""Tests for article dedup + recency-sort in the News analyst context builder.

These tests exercise the new ``_dedup_and_sort_articles`` helper added to
``agents.analysts.news.fetch``.  They are written to FAIL before the
implementation lands (strict TDD), and should all pass once the dedup +
recency-sort logic is in place.

Coverage:
  (1) Dedup — 100 near-duplicate rehashes + 5 distinct fresh stories → 6 articles
      (one representative per cluster); representative kept is freshest in cluster.
  (2) Recency-sort — articles rendered freshest-first; ``As of:`` anchor and
      ``Nd ago`` annotations remain correct.
  (3) AMD-like scenario — fresh distinct stories surface at the top; stale
      rehash bulk is collapsed.
  (4) Config-driven — dedup threshold is read from config (not hardcoded).
  (5) Loud failure — non-empty input where all timestamps are unparseable raises
      rather than silently emitting an empty/``no news`` block.
  (6) Representative selection — when multiple articles share the same normalised
      title, the FRESHEST one's headline text is kept in the output (not merely
      any representative).
  (7) Stable / deterministic output — equal-timestamp tie-breaks must produce
      the same ordering every call (no randomness).
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agents.analysts.news.fetch import (
    _build_ticker_news_context,
    _dedup_and_sort_articles,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_article(
    title: str = "Headline",
    summary: str = "Short summary.",
    published_at: str | None = "2025-10-05",
    source: str = "Reuters",
) -> dict:
    """Return a minimal article dict as produced by the provider serialiser.

    Parameters
    ----------
    title:
        Article headline.
    summary:
        Article body excerpt.
    published_at:
        ISO date/datetime string, or ``None`` for unknown.
    source:
        News source label (unused by dedup logic itself, included for
        completeness and future authority-ranking work).

    Returns
    -------
    dict
        Minimal article dict compatible with the fetch-module renderer.
    """
    return {
        "title":        title,
        "summary":      summary,
        "published_at": published_at,
        "source":       source,
    }


def _make_fake_caps(
    max_articles: int = 50,
    max_generic: int = 10,
    max_summary_chars: int = 200,
    roundup_company_threshold: int = 3,
    dedup_title_similarity_threshold: float = 0.85,
) -> MagicMock:
    """Return a ``MagicMock`` that mimics a ``NewsCaps`` object.

    Includes the new ``dedup_title_similarity_threshold`` attribute so that
    the dedup helper can read it from the mock without hitting the real
    config file.

    Parameters
    ----------
    max_articles:
        Value for ``max_articles_per_ticker``.
    max_generic:
        Value for ``max_generic_articles_per_ticker``.
    max_summary_chars:
        Value for ``max_summary_chars``.
    roundup_company_threshold:
        Roundup-demotion threshold.
    dedup_title_similarity_threshold:
        Similarity ratio [0–1] above which two titles are considered
        near-duplicates.  Default 0.85.

    Returns
    -------
    MagicMock
        A mock with all ``NewsCaps`` attributes set.
    """
    caps = MagicMock()
    caps.max_articles_per_ticker            = max_articles
    caps.max_generic_articles_per_ticker    = max_generic
    caps.max_summary_chars                  = max_summary_chars
    caps.roundup_company_threshold          = roundup_company_threshold
    caps.dedup_title_similarity_threshold   = dedup_title_similarity_threshold
    return caps


def _build_context(
    articles: list[dict],
    *,
    as_of: datetime,
    ticker: str = "AMD",
    company_name: str | None = "AMD",
    max_articles: int = 50,
    max_generic: int = 10,
    dedup_threshold: float = 0.85,
) -> str:
    """Call ``_build_ticker_news_context`` with config + universe patched.

    Isolates tests from the live watchlist + config files.

    Parameters
    ----------
    articles:
        Article list to pass through.
    as_of:
        Reference datetime for age computation.
    ticker:
        Ticker symbol; defaults to ``"AMD"``.
    company_name:
        Optional company name for specificity matching.
    max_articles:
        Override for ``max_articles_per_ticker`` on the fake caps.
    max_generic:
        Override for ``max_generic_articles_per_ticker`` on the fake caps.
    dedup_threshold:
        Similarity threshold passed to the fake caps.

    Returns
    -------
    str
        The rendered context block.
    """
    fake_caps = _make_fake_caps(
        max_articles=max_articles,
        max_generic=max_generic,
        dedup_title_similarity_threshold=dedup_threshold,
    )

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        return _build_ticker_news_context(
            ticker, articles, as_of=as_of, company_name=company_name,
        )


# ---------------------------------------------------------------------------
# (1) Dedup — cluster of 100 rehashes + 5 distinct stories → 6 articles
# ---------------------------------------------------------------------------


def test_dedup_collapses_rehash_cluster_to_one_article():
    """100 near-identical rehashes plus 5 distinct stories must yield exactly 6.

    The 100 articles all share the same normalised title (only source/trailing
    punctuation differs — a classic syndication pattern).  The 5 distinct
    stories each have unique titles.  After dedup, exactly one representative
    remains for the rehash cluster (the freshest one), plus one per distinct
    story = 6 total.
    """
    base_title = "AMD Unveils Next-Gen Instinct MI400 GPU at Hot Chips"

    # 100 rehash variants — capitalisation, punctuation, and source tags differ
    # but the core title is identical after normalisation.
    rehash_articles = [
        _make_article(
            title=f"{base_title} (Reuters)",
            published_at="2025-09-21",  # all stale
            source="Reuters",
        )
        for _ in range(50)
    ] + [
        _make_article(
            title=f"{base_title}.",          # trailing period variant
            published_at="2025-09-21",
            source="Bloomberg",
        )
        for _ in range(30)
    ] + [
        _make_article(
            title=base_title.upper(),        # all-caps variant
            published_at="2025-09-22",       # freshest in the cluster
            source="CNBC",
        )
        for _ in range(20)
    ]

    # Five articles with genuinely distinct titles — each covers a completely
    # different subject so the normalised forms share no long common substring
    # and will not collapse into a cluster with each other.
    distinct_articles = [
        _make_article(
            title="AMD CEO discusses roadmap at Goldman Sachs Technology Conference",
            published_at="2025-09-22",
            source="WSJ",
        ),
        _make_article(
            title="Regulatory filing shows AMD acquired AI startup for two billion dollars",
            published_at="2025-09-22",
            source="FT",
        ),
        _make_article(
            title="Short sellers increase bets against semiconductor sector leaders",
            published_at="2025-09-22",
            source="Barron's",
        ),
        _make_article(
            title="Data centre operators signal preference for custom silicon over merchant GPUs",
            published_at="2025-09-22",
            source="The Register",
        ),
        _make_article(
            title="European Union opens antitrust probe into AI chip supply agreements",
            published_at="2025-09-22",
            source="Politico",
        ),
    ]

    all_articles = rehash_articles + distinct_articles

    # Patch caps so the total cap is generous (100) — the 6-article result must
    # come purely from dedup, not from the total cap.
    fake_caps = _make_fake_caps(max_articles=100, max_generic=100)

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        result = _dedup_and_sort_articles(all_articles)

    assert len(result) == 6, (
        f"Expected 6 articles after dedup (1 cluster rep + 5 distinct); got {len(result)}"
    )


def test_dedup_representative_is_freshest_in_cluster():
    """The representative kept for a duplicate cluster must be the freshest article.

    When the same story appears as multiple articles, the one with the most
    recent ``published_at`` timestamp should be the survivor.  All other
    members of the cluster should be discarded.
    """
    base_title = "Nvidia tops quarterly earnings expectations"

    # Three rehashes with different timestamps — we expect the one from 2025-10-10
    # (the freshest) to be retained.
    old_version    = _make_article(title=base_title, published_at="2025-10-08", source="AP")
    stale_version  = _make_article(title=base_title, published_at="2025-10-07", source="Reuters")
    fresh_version  = _make_article(title=base_title + ".", published_at="2025-10-10", source="CNBC")

    fake_caps = _make_fake_caps(max_articles=50, max_generic=50)

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        result = _dedup_and_sort_articles([old_version, stale_version, fresh_version])

    assert len(result) == 1, (
        f"Three rehashes of the same title should collapse to 1; got {len(result)}"
    )
    # The survivor must be the freshest article (published_at 2025-10-10).
    assert result[0]["published_at"] == "2025-10-10", (
        f"Surviving representative should be the freshest (2025-10-10); "
        f"got published_at={result[0]['published_at']!r}"
    )


# ---------------------------------------------------------------------------
# (2) Recency-sort — articles rendered freshest-first
# ---------------------------------------------------------------------------


def test_recency_sort_renders_freshest_first():
    """After dedup, the rendered block must list articles freshest-first.

    Given three articles published at 2025-10-08, 2025-10-10, 2025-10-09 (in
    that order in the input list), the rendered block must order them
    2025-10-10 → 2025-10-09 → 2025-10-08.

    Titles are deliberately long and distinct enough that the SequenceMatcher
    similarity between any two normalised forms is well below 0.85 — they
    must not cluster together.
    """
    articles = [
        _make_article(
            title="Oil prices fall sharply as OPEC production deal collapses overnight",
            published_at="2025-10-08",  # oldest
        ),
        _make_article(
            title="Central bank raises interest rates by fifty basis points citing persistent inflation",
            published_at="2025-10-10",  # freshest
        ),
        _make_article(
            title="Government approves new defence spending package worth three hundred billion",
            published_at="2025-10-09",  # middle
        ),
    ]

    as_of = datetime(2025, 10, 11, 16, 0)
    result = _build_context(articles, as_of=as_of)

    pos_fresh  = result.index("Central bank raises interest rates")   # 2025-10-10
    pos_middle = result.index("Government approves new defence")      # 2025-10-09
    pos_oldest = result.index("Oil prices fall sharply")              # 2025-10-08

    assert pos_fresh < pos_middle < pos_oldest, (
        f"Expected freshest-first order fresh < middle < oldest; "
        f"got positions fresh={pos_fresh}, middle={pos_middle}, oldest={pos_oldest}\n\n{result}"
    )


def test_recency_sort_preserves_as_of_anchor():
    """The ``As of:`` anchor must still be present after dedup + recency-sort."""
    articles = [
        _make_article(title="AMD earnings beat", published_at="2025-10-09"),
    ]
    as_of = datetime(2025, 10, 11)

    result = _build_context(articles, as_of=as_of)

    assert "As of: 2025-10-11" in result, (
        f"Expected 'As of: 2025-10-11' anchor in block;\ngot:\n{result}"
    )


def test_recency_sort_preserves_age_annotations():
    """``Nd ago`` annotations must be correct after recency-sort."""
    articles = [
        _make_article(title="AMD earnings beat", published_at="2025-10-09"),
        _make_article(title="AMD CEO interview", published_at="2025-10-08"),
    ]
    as_of = datetime(2025, 10, 11)

    result = _build_context(articles, as_of=as_of)

    # First article (2025-10-09) is 2 days before as_of 2025-10-11.
    assert "2d ago" in result, (
        f"Expected '2d ago' for article published 2 days before as_of;\ngot:\n{result}"
    )
    # Second article (2025-10-08) is 3 days before as_of.
    assert "3d ago" in result, (
        f"Expected '3d ago' for article published 3 days before as_of;\ngot:\n{result}"
    )


# ---------------------------------------------------------------------------
# (3) AMD-like scenario — fresh stories surface first, stale bulk collapses
# ---------------------------------------------------------------------------


def test_amd_like_scenario_fresh_stories_at_top():
    """AMD-like scenario: 116 stale rehashes + 16 fresh distinct stories.

    After dedup + recency-sort:
      - The stale rehashes collapse to at most 1 representative (or a small
        handful if there are a few distinct stale titles).
      - The 16 fresh distinct stories appear BEFORE any stale content in the
        rendered output.

    This mirrors the observed AMD 2025-09-22 failure where 116 stale articles
    drowned 16 fresh ones and the model emitted ``novelty:high`` confidence 0.8.
    """
    stale_title = "AMD MI300X GPU Leads in AI Benchmark Results"

    # 100 stale rehashes of a single old story.
    stale_rehashes = [
        _make_article(
            title=stale_title if i % 2 == 0 else stale_title + ".",
            published_at="2025-09-10",  # 12 days stale relative to as_of
            source=f"Outlet{i}",
        )
        for i in range(100)
    ]

    # 16 distinct fresh stories (published on the as_of day).
    # Titles use completely different subjects so they do not cluster together.
    fresh_distinct = [
        _make_article(
            title="AMD chief executive presents keynote at Deutsche Bank Tech Forum",
            published_at="2025-09-22",
            source="Bloomberg",
        ),
        _make_article(
            title="Server vendors report record GPU shipments driven by AI spending",
            published_at="2025-09-22",
            source="Digitimes",
        ),
        _make_article(
            title="Pension fund discloses new stake in semiconductor equipment sector",
            published_at="2025-09-22",
            source="13F filing",
        ),
        _make_article(
            title="Software developers report faster inference times on latest GPU generation",
            published_at="2025-09-22",
            source="HPC Wire",
        ),
        _make_article(
            title="Competition between cloud providers intensifies over AI training capacity",
            published_at="2025-09-22",
            source="TechCrunch",
        ),
        _make_article(
            title="Supply chain report signals tight TSMC capacity through end of year",
            published_at="2025-09-22",
            source="DigiTimes",
        ),
        _make_article(
            title="Memory bandwidth becomes primary bottleneck for large language model training",
            published_at="2025-09-22",
            source="Wired",
        ),
        _make_article(
            title="Federal contracts for defence AI applications shift toward domestic suppliers",
            published_at="2025-09-22",
            source="DefenseNews",
        ),
        _make_article(
            title="Open source inference framework gains adoption among enterprise customers",
            published_at="2025-09-22",
            source="VentureBeat",
        ),
        _make_article(
            title="Investor day presentation outlines five year capital allocation priorities",
            published_at="2025-09-22",
            source="IR website",
        ),
        _make_article(
            title="Analysts raise price targets citing expanding data centre total addressable market",
            published_at="2025-09-22",
            source="Seeking Alpha",
        ),
        _make_article(
            title="Workforce expansion plan targets software engineers and silicon architects",
            published_at="2025-09-22",
            source="LinkedIn News",
        ),
        _make_article(
            title="Patent filings reveal next generation memory subsystem architecture details",
            published_at="2025-09-22",
            source="Ars Technica",
        ),
        _make_article(
            title="International sales face headwinds from updated export licensing requirements",
            published_at="2025-09-22",
            source="Reuters",
        ),
        _make_article(
            title="Earnings call transcript highlights margin expansion expectations from CFO",
            published_at="2025-09-22",
            source="Motley Fool",
        ),
        _make_article(
            title="University research partnership announced for heterogeneous compute architectures",
            published_at="2025-09-22",
            source="EE Times",
        ),
    ]

    # Confirm we have exactly 16 distinct fresh articles.
    assert len(fresh_distinct) == 16

    all_articles = stale_rehashes + fresh_distinct
    as_of = datetime(2025, 9, 22, 16, 0)

    # Use a generous cap so we see the dedup effect, not cap truncation.
    result = _build_context(
        all_articles,
        as_of=as_of,
        ticker="AMD",
        company_name="AMD",
        max_articles=50,
        max_generic=50,
    )

    # Sample of fresh titles that must appear in the output.
    fresh_sample_titles = [
        "AMD chief executive presents keynote",
        "Server vendors report record GPU shipments",
        "Software developers report faster inference",
    ]
    for expected_fragment in fresh_sample_titles:
        assert expected_fragment in result, (
            f"Fresh story fragment {expected_fragment!r} missing from rendered block:\n{result[:800]}"
        )

    # The stale rehash cluster must be collapsed to at most 2 entries
    # (not 100 or anywhere near it).
    stale_match_count = result.count("AMD MI300X GPU Leads")
    assert stale_match_count <= 2, (
        f"Expected stale rehash cluster to be collapsed to ≤ 2 entries; "
        f"found {stale_match_count} occurrences of the stale title in the block"
    )

    # Fresh stories (0d ago) must appear before the stale representative (12d ago).
    fresh_pos  = result.index("AMD chief executive presents keynote")
    stale_pos  = result.index("AMD MI300X GPU")

    assert fresh_pos < stale_pos, (
        f"Fresh stories should precede the stale cluster representative; "
        f"fresh_pos={fresh_pos}, stale_pos={stale_pos}"
    )


# ---------------------------------------------------------------------------
# (4) Config-driven — threshold is read from config, not hardcoded
# ---------------------------------------------------------------------------


def test_dedup_threshold_is_config_driven():
    """The similarity threshold must be read from config, not hardcoded.

    At threshold 1.0 (exact match only), two slightly different titles should
    NOT be collapsed into one cluster — they are distinct.  At threshold 0.7
    (lenient), they should collapse.

    This verifies that the threshold parameter is actually honoured.
    """
    # Two titles that are close but not identical — differ by one word.
    title_a = "AMD Instinct MI400 GPU beats Nvidia in AI inference benchmark"
    title_b = "AMD Instinct MI400 GPU beats Nvidia in AI inference tests"

    articles = [
        _make_article(title=title_a, published_at="2025-10-09"),
        _make_article(title=title_b, published_at="2025-10-10"),
    ]

    fake_caps_strict = _make_fake_caps(
        max_articles=50, max_generic=50,
        dedup_title_similarity_threshold=1.0,  # exact-match only
    )

    fake_caps_lenient = _make_fake_caps(
        max_articles=50, max_generic=50,
        dedup_title_similarity_threshold=0.7,  # high-similarity collapse
    )

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps_strict),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        result_strict = _dedup_and_sort_articles(articles)

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps_lenient),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        result_lenient = _dedup_and_sort_articles(articles)

    assert len(result_strict) == 2, (
        f"At threshold=1.0 (exact only), two distinct titles should both survive; "
        f"got {len(result_strict)}"
    )

    assert len(result_lenient) == 1, (
        f"At threshold=0.7 (lenient), two near-duplicate titles should collapse to 1; "
        f"got {len(result_lenient)}"
    )


# ---------------------------------------------------------------------------
# (5) Loud failure — all timestamps unparseable → raise, not silent empty
# ---------------------------------------------------------------------------


def test_all_unparseable_timestamps_with_titles_renders_age_unknown():
    """Articles with missing timestamps but valid titles must render as 'age unknown'.

    This is the valid "age unknown" path: articles exist, they have titles, but
    their timestamps cannot be parsed (e.g. ``MISSING_TIMESTAMP`` sentinel from
    the provider).  The dedup helper should NOT raise here — it should proceed
    gracefully so the renderer can show the headlines with an "age unknown" label.

    This is the correct behaviour used by the existing test suite and must not
    regress.
    """
    bad_timestamp_articles = [
        _make_article(title="AMD news announcement one", published_at="MISSING_TIMESTAMP"),
        _make_article(title="AMD quarterly earnings call date announced", published_at="not-a-date"),
        _make_article(title="AMD product launch scheduled for next quarter", published_at="???"),
    ]

    fake_caps = _make_fake_caps(max_articles=50, max_generic=50)

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        # Must NOT raise — should return the articles (or at least one cluster
        # representative) so the renderer can show "age unknown".
        result = _dedup_and_sort_articles(bad_timestamp_articles)

    assert len(result) >= 1, (
        "Articles with unparseable timestamps but valid titles should survive dedup; "
        "got empty result"
    )


def test_all_unparseable_timestamps_and_no_titles_raises():
    """A non-empty article list with no timestamps AND no titles must raise.

    This is the degenerate-feed scenario: systematically broken data where
    neither timestamps nor titles are present.  Silent degradation would
    produce output indistinguishable from the "no news available" path while
    hiding a systematic provider failure.

    ``ValueError`` is the expected exception type.
    """
    # Articles with neither a useful title nor a parseable timestamp.
    broken_articles = [
        {"title": "", "summary": "Some text.", "published_at": "MISSING_TIMESTAMP"},
        {"title": "", "summary": "Other text.", "published_at": "not-a-date"},
        {"headline": None, "summary": "More text.", "published_at": "???"},
    ]

    fake_caps = _make_fake_caps(max_articles=50, max_generic=50)

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
        pytest.raises(ValueError, match="timestamp"),
    ):
        _dedup_and_sort_articles(broken_articles)


# ---------------------------------------------------------------------------
# (6) Representative selection — freshest article's title text is kept
# ---------------------------------------------------------------------------


def test_representative_headline_text_is_kept_for_cluster():
    """The representative kept for a cluster should carry the freshest title's text.

    Even if the oldest article in the cluster has a slightly different title,
    the output entry must use the freshest article's exact title text (since
    the freshest version is the canonical representation of the story, and
    source differences mean the freshest may have better/corrected phrasing).
    """
    # Old article has a slightly wrong headline; fresh version corrected it.
    old_article   = _make_article(
        title="AMD Instinct MI400 GPU Beats Nvidia in AI Benchmarks (BREAKING)",
        published_at="2025-10-08",
    )
    fresh_article = _make_article(
        title="AMD Instinct MI400 GPU beats Nvidia in AI benchmarks",
        published_at="2025-10-10",
    )

    fake_caps = _make_fake_caps(max_articles=50, max_generic=50)

    with (
        patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
        patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
    ):
        result = _dedup_and_sort_articles([old_article, fresh_article])

    assert len(result) == 1, f"Two near-duplicates should collapse to 1; got {len(result)}"

    surviving_title = result[0].get("title") or result[0].get("headline")

    assert surviving_title == fresh_article["title"], (
        f"Surviving article should carry the FRESH title; "
        f"got: {surviving_title!r}"
    )


# ---------------------------------------------------------------------------
# (7) Determinism — equal-timestamp tie-breaks produce stable ordering
# ---------------------------------------------------------------------------


def test_dedup_is_deterministic_on_equal_timestamps():
    """When two articles have identical timestamps, the output order must be stable.

    Running the dedup helper multiple times with the same input must always
    produce the same output ordering — no random or hash-dependent shuffling.
    """
    articles = [
        _make_article(title=f"Article {i}", published_at="2025-10-10")
        for i in range(10)
    ]

    fake_caps = _make_fake_caps(max_articles=50, max_generic=50)

    results = []
    for _ in range(5):
        with (
            patch("agents.analysts.news.fetch._caps", return_value=fake_caps),
            patch("agents.analysts.news.fetch._watchlist_universe", return_value=[]),
        ):
            run_result = _dedup_and_sort_articles(list(articles))  # fresh copy each time
        results.append([a["title"] for a in run_result])

    # All five runs must produce the same ordering.
    for i in range(1, 5):
        assert results[0] == results[i], (
            f"Dedup produced different orderings on run {i}:\n"
            f"  Run 0: {results[0]}\n"
            f"  Run {i}: {results[i]}"
        )
