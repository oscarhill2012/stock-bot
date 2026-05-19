"""Integration tests: report-cache lifecycle for the News and Fundamental analysts.

Both analysts share the same cache-callback factory
(``make_report_cache_callbacks``) and therefore exhibit the same lifecycle
behaviour.  Three scenarios are exercised:

1. **roundtrip**       — identical inputs on a 2nd run short-circuit the LLM.
2. **invalidation**    — mutating the upstream data busts the cache.
3. **prompt_version**  — bumping the ``prompt_version`` string busts the cache.

Previously these were six separate files (three scenarios × two analysts) that
shared ~95% of their scaffolding.  This module collapses them into one
parameterised suite: each analyst is described by a ``CacheDomain`` spec and
the three tests are run once per spec.

The ``cache_root`` fixture and ``make_ctx`` helper live in
``tests/integration/conftest.py`` and are auto-discovered by pytest.  The
``_after`` hook reads verdicts from ``llm_response.content`` directly (the B22
bug-fix), so the tests pass a synthetic response object via
``_fake_llm_response``.
"""
from __future__ import annotations

import json
import types
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

import pytest

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.fundamental.agent import _fundamental_hash_inputs_from_dict
from agents.analysts.report_cache import (
    FUNDAMENTAL_PROMPT_VERSION,
    NEWS_PROMPT_VERSION,
    news_hash_inputs,
)
from data.models import NewsArticle
from tests.integration.conftest import make_ctx


# ---------------------------------------------------------------------------
# Shared fake-LLM-response helper
# ---------------------------------------------------------------------------

def _fake_llm_response(verdicts: list[dict]) -> types.SimpleNamespace:
    """Build a minimal fake LLM response whose content matches the factory's expectations.

    The ``_after`` hook reads ``llm_response.content.parts[0].text`` for the
    JSON payload.  This stub keeps tests free of any ``google.genai`` imports.

    Parameters
    ----------
    verdicts:
        List of verdict dicts to embed in the JSON payload.

    Returns
    -------
    types.SimpleNamespace
        A stub whose ``.content.parts[0].text`` is the serialised payload.
    """
    text    = json.dumps({"verdicts": verdicts})
    part    = types.SimpleNamespace(text=text)
    content = types.SimpleNamespace(parts=[part])
    return types.SimpleNamespace(content=content)


# ---------------------------------------------------------------------------
# Domain spec — captures everything that differs between the two analysts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CacheDomain:
    """One row of the parameterised test matrix — describes a cache-using analyst.

    Attributes
    ----------
    id:
        Short human-readable identifier used for pytest parametrize IDs.
    default_prompt_version:
        The module-level prompt-version constant for this analyst (used by the
        roundtrip and invalidation tests; the prompt-version test passes a
        different string).
    verdicts_state_key:
        State key under which verdicts are persisted by the ``_after`` hook.
        Used both to clear the key between runs and to verify hit semantics.
    make_callbacks:
        Factory that returns ``(before, after)`` cache callbacks for this
        analyst.  Accepts an optional ``prompt_version`` so the prompt-version
        bump test can build a second callback pair with a different version.
    initial_state:
        Factory that returns a fresh, mutable state dict ready for the first
        ``before`` call.  A factory (not a literal) keeps every test fully
        isolated even though they share a parametrize spec.
    mutate_for_invalidation:
        In-place mutation that changes the upstream data such that the input
        hash diverges from the persisted entry.  Used by the invalidation
        test.
    verdicts_payload:
        Factory that returns the canonical verdicts payload the LLM would
        have produced — used to populate the cache on the first run.
    """

    id: str
    default_prompt_version: str
    verdicts_state_key: str
    make_callbacks: Callable[..., tuple[Callable, Callable]]
    initial_state: Callable[[], dict]
    mutate_for_invalidation: Callable[[dict], None]
    verdicts_payload: Callable[[], list[dict]]


# ---------------------------------------------------------------------------
# News domain — articles list keyed by ticker
# ---------------------------------------------------------------------------

def _make_news_callbacks(prompt_version: str = NEWS_PROMPT_VERSION):
    """Construct news cache callbacks via the shared factory.

    Mirrors the wiring in ``_build_news_analyst`` so the integration tests
    exercise the same factory path as the real agent.
    """
    return make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = prompt_version,
        data_state_key     = "news_data",
        verdicts_state_key = "news_verdicts",
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
        trace_label        = "03_news_llm",
    )


def _news_initial_state() -> dict:
    """Fresh state dict for the News analyst — one ticker, one article."""

    articles = [
        NewsArticle(
            url          = "https://x",
            headline     = "t",
            summary      = "s",
            published_at = "2026-05-13T10:00:00",
            source       = "src",
            ticker       = "AAPL",
        ).model_dump()
    ]
    return {
        "tickers":   ["AAPL"],
        "news_data": {"AAPL": {"news": articles}},
    }


def _news_mutate(state: dict) -> None:
    """Append a brand-new article so the input hash diverges."""

    new_article = NewsArticle(
        url          = "https://c",
        headline     = "breaking",
        summary      = "big news",
        published_at = "2026-05-13T12:00:00",
        source       = "src",
        ticker       = "AAPL",
    ).model_dump()
    state["news_data"]["AAPL"]["news"].append(new_article)


def _news_verdicts() -> list[dict]:
    """Canonical news verdicts payload the LLM would have returned."""

    return [{
        "ticker":      "AAPL",
        "lean":        "neutral",
        "magnitude":   0.3,
        "confidence":  0.7,
        "rationale":   "x",
        "key_factors": [],
        "is_no_data":  False,
        "report": {
            "summary": "s",
            "drivers": [
                {"name": "n1", "direction": "neutral", "weight": 0.5, "body": "body one"},
                {"name": "n2", "direction": "neutral", "weight": 0.5, "body": "body two"},
            ],
        },
    }]


# ---------------------------------------------------------------------------
# Fundamental domain — (ratios, filings, insider) triad keyed by ticker
# ---------------------------------------------------------------------------

def _make_fundamental_callbacks(prompt_version: str = FUNDAMENTAL_PROMPT_VERSION):
    """Construct fundamental cache callbacks via the shared factory.

    The hash function reconstructs typed ``CompanyRatios``, ``list[Filing]``,
    and ``Form4Bundle`` objects from the dicts in
    ``state["fundamental_data"][ticker]`` before computing the digest, so the
    test fixtures store the model-dump dict shapes directly.
    """
    return make_report_cache_callbacks(
        analyst_name       = "fundamental",
        prompt_version     = prompt_version,
        data_state_key     = "fundamental_data",
        verdicts_state_key = "fundamental_verdicts",
        hash_inputs        = lambda d: _fundamental_hash_inputs_from_dict(
            ticker = ((d or {}).get("ratios") or {}).get("ticker", ""),
            triad  = (d or {}),
        ),
        trace_label        = "03_fundamental_llm",
    )


def _minimal_filing(accession_no: str) -> dict:
    """Return a minimal ``Filing.model_dump()``-compatible dict.

    The Fundamental hash function keys on accession numbers, so the accession
    number is the only field that affects cache identity in this test.

    Parameters
    ----------
    accession_no:
        Unique SEC accession number for this filing.
    """
    return {
        "ticker":               "AAPL",
        "form_type":            "10-Q",
        "filed_at":             datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC).isoformat(),
        "accession_no":         accession_no,
        "url":                  f"https://sec.gov/Archives/{accession_no}.htm",
        "mda_excerpt":          None,
        "risk_factors_excerpt": None,
    }


def _fundamental_initial_state() -> dict:
    """Fresh state dict for the Fundamental analyst — one ticker, one filing."""

    return {
        "tickers": ["AAPL"],
        "fundamental_data": {
            "AAPL": {
                "ratios":  {"ticker": "AAPL", "trailing_pe": 28.5},
                "filings": [_minimal_filing("0000320193-26-000010")],
                "insider": None,
            }
        },
    }


def _fundamental_mutate(state: dict) -> None:
    """Append a new filing with a fresh accession number to bust the hash."""

    state["fundamental_data"]["AAPL"]["filings"].append(
        _minimal_filing("0000320193-26-000099")
    )


def _fundamental_verdicts() -> list[dict]:
    """Canonical fundamental verdicts payload the LLM would have returned.

    ``direction`` uses ``"bull"``/``"bear"``/``"neutral"`` per the
    ``ReportDriver`` schema; ``lean`` uses ``"bullish"``/``"bearish"``/
    ``"neutral"`` per the ``AnalystVerdict`` schema.
    """
    return [{
        "ticker":      "AAPL",
        "lean":        "bullish",
        "magnitude":   0.55,
        "confidence":  0.75,
        "rationale":   "Strong quarterly results.",
        "key_factors": [],
        "is_no_data":  False,
        "report": {
            "summary": "Quarterly filing shows continued top-line growth.",
            "drivers": [
                {"name": "Revenue beat",       "direction": "bull",    "weight": 0.6, "body": "Revenue exceeded consensus by 4%."},
                {"name": "PE still stretched", "direction": "neutral", "weight": 0.4, "body": "Trailing PE of 28 is above the sector average."},
            ],
        },
    }]


# ---------------------------------------------------------------------------
# Parametrize matrix
# ---------------------------------------------------------------------------

DOMAINS = [
    CacheDomain(
        id                      = "news",
        default_prompt_version  = NEWS_PROMPT_VERSION,
        verdicts_state_key      = "news_verdicts",
        make_callbacks          = _make_news_callbacks,
        initial_state           = _news_initial_state,
        mutate_for_invalidation = _news_mutate,
        verdicts_payload        = _news_verdicts,
    ),
    CacheDomain(
        id                      = "fundamental",
        default_prompt_version  = FUNDAMENTAL_PROMPT_VERSION,
        verdicts_state_key      = "fundamental_verdicts",
        make_callbacks          = _make_fundamental_callbacks,
        initial_state           = _fundamental_initial_state,
        mutate_for_invalidation = _fundamental_mutate,
        verdicts_payload        = _fundamental_verdicts,
    ),
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("domain", DOMAINS, ids=[d.id for d in DOMAINS])
def test_second_run_hits_cache(cache_root, domain: CacheDomain):
    """Identical inputs on two consecutive runs -> 2nd run short-circuits the LLM."""

    before, after = domain.make_callbacks()
    ctx           = make_ctx(domain.initial_state())

    # First run — cache miss, before returns None so the LLM would run.
    assert before(ctx, llm_request=None) is None

    # Persist the synthetic LLM verdict to disk (the after hook now reads from
    # llm_response.content directly — see B22 bug-fix).
    after(ctx, llm_response=_fake_llm_response(domain.verdicts_payload()))

    # Drop the verdict key so the cache hit has to repopulate it.
    ctx.state.pop(domain.verdicts_state_key, None)

    # Second run with identical inputs — cache hit short-circuits the LLM.
    short_circuit = before(ctx, llm_request=None)
    assert short_circuit is not None, (
        f"[{domain.id}] expected cache hit on the 2nd run with identical inputs, "
        f"but before-callback returned None (unexpected cache miss)."
    )

    # The cached verdicts must have been written back into state.
    assert domain.verdicts_state_key in ctx.state, (
        f"[{domain.id}] cache hit did not populate state[{domain.verdicts_state_key!r}]."
    )
    assert ctx.state[domain.verdicts_state_key]["verdicts"][0]["ticker"] == "AAPL", (
        f"[{domain.id}] cached verdict ticker does not match expected value 'AAPL'."
    )


@pytest.mark.parametrize("domain", DOMAINS, ids=[d.id for d in DOMAINS])
def test_input_mutation_busts_cache(cache_root, domain: CacheDomain):
    """Mutating upstream data between runs must produce a cache miss on the 2nd run."""

    before, after = domain.make_callbacks()
    ctx           = make_ctx(domain.initial_state())

    # First run — miss, then persist the verdict.
    assert before(ctx, llm_request=None) is None
    after(ctx, llm_response=_fake_llm_response(domain.verdicts_payload()))

    # Apply the domain-specific mutation (new article / new filing).
    domain.mutate_for_invalidation(ctx.state)
    ctx.state.pop(domain.verdicts_state_key, None)

    # The input hash has changed -> cache miss -> before must return None.
    result = before(ctx, llm_request=None)
    assert result is None, (
        f"[{domain.id}] expected cache miss after mutating upstream data, "
        f"but before-callback returned non-None (spurious cache hit)."
    )


@pytest.mark.parametrize("domain", DOMAINS, ids=[d.id for d in DOMAINS])
def test_prompt_version_bump_busts_cache(cache_root, domain: CacheDomain):
    """Bumping the prompt version between runs must produce a cache miss on the 2nd run.

    The first ``before/after`` pair writes the entry under the analyst's
    default prompt version.  A second pair, built with a different version
    string, no longer matches the persisted entry -> miss.
    """

    before, after = domain.make_callbacks(domain.default_prompt_version)
    ctx           = make_ctx(domain.initial_state())

    # First run with the original prompt version — miss, then persist.
    assert before(ctx, llm_request=None) is None
    after(ctx, llm_response=_fake_llm_response(domain.verdicts_payload()))

    # Second run with a bumped prompt-version string — must miss.
    before2, _ = domain.make_callbacks("v-test-bump")
    ctx.state.pop(domain.verdicts_state_key, None)

    result = before2(ctx, llm_request=None)
    assert result is None, (
        f"[{domain.id}] expected cache miss after bumping prompt_version, "
        f"but before-callback returned non-None (spurious cache hit)."
    )
