"""Upstream verifier must distinguish "ran and agreed" from "did not run".

Plan 10 §4 — no green-on-skip rendering.
"""
from __future__ import annotations

from types import SimpleNamespace

from backtest.audit.upstream_verifier import _verify_filing, _verify_news


def test_verify_filing_returns_skip_when_no_accession():
    """A filing row with no accession_no cannot be verified — status must
    be 'skip', not 'ok'."""
    row = SimpleNamespace(accession_no=None, id=None)
    result = _verify_filing(row)
    assert result["verification_status"] == "skip"
    assert "agreement_with_cache" not in result, (
        "Boolean agreement field must be removed — replaced by tri-state "
        "verification_status."
    )


def test_verify_filing_placeholder_returns_skip_even_with_accession():
    """Until the real sec.gov fetcher is wired, the body must self-report
    as a skip — never green-on-placeholder."""
    row = SimpleNamespace(accession_no="0001234567-26-000001")
    result = _verify_filing(row)
    assert result["verification_status"] == "skip"


def test_verify_news_placeholder_returns_skip():
    """Same contract for news — placeholder body must self-report skip."""
    row = SimpleNamespace(url="https://example.com/article")
    result = _verify_news(row)
    assert result["verification_status"] == "skip"


def test_summary_renderer_counts_skip_separately_from_ok():
    """The SUMMARY must not collapse 'skip' into the 'verified' bucket."""
    from backtest.audit.deep_dump import summarise_verification_states
    counts = summarise_verification_states([
        {"verification_status": "ok"},
        {"verification_status": "ok"},
        {"verification_status": "skip"},
        {"verification_status": "disagree"},
    ])
    assert counts == {"ok": 2, "skip": 1, "disagree": 1}
