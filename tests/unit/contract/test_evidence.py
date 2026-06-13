"""AnalystVerdict + AnalystEvidence schema tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystEvidence, AnalystVerdict


def _verdict(**overrides) -> AnalystVerdict:
    """Build an AnalystVerdict respecting the exactly-one-prose-surface invariant.

    Defaults to a deterministic-extractor style verdict (rationale non-empty,
    ``report=None``).  Callers can override any field; the three valid
    configurations are:

    - ``is_no_data=True``       → no-data short-circuit; ``report`` forced
                                   to ``None``.
    - ``report`` is a non-None  → LLM-style; ``rationale`` forced to ``""``
                                   so the exactly-one check passes.
    - otherwise (default)       → deterministic; ``report`` stays ``None``
                                   and ``rationale`` carries the one-liner.

    Parameters
    ----------
    **overrides:
        Field overrides applied on top of the deterministic-style defaults.

    Returns
    -------
    AnalystVerdict
        Fully-formed verdict satisfying the exactly-one-prose-surface invariant.
    """
    base: dict = dict(
        lean="bullish",
        magnitude=0.5,
        confidence=0.7,
        rationale="RSI cooled + uptrend intact",
        key_factors=["rsi_14: 42"],
        is_no_data=False,
        report=None,
    )
    base.update(overrides)

    # Enforce the three mutually-exclusive prose-surface branches.
    if base["is_no_data"]:
        # No-data short-circuit — report must be absent.
        base["report"] = None
    elif base["report"] is not None:
        # LLM-style: report is the prose surface; blank rationale field.
        base["rationale"] = ""
    else:
        # Deterministic extractor: rationale is the prose surface; no report.
        pass  # defaults already correct

    return AnalystVerdict(**base)


def _now() -> datetime:
    return datetime(2026, 5, 8, 14, 0, tzinfo=UTC)


def test_verdict_valid():
    v = _verdict()
    assert v.lean == "bullish"
    assert v.magnitude == 0.5
    assert v.confidence == 0.7
    assert v.key_factors == ["rsi_14: 42"]
    assert v.is_no_data is False


def test_verdict_neutral_no_data_flag():
    v = _verdict(lean="neutral", magnitude=0.0, confidence=0.0,
                 rationale="no filings", key_factors=[], is_no_data=True)
    assert v.is_no_data is True


def test_verdict_key_factors_default_empty():
    # Use is_no_data=True so the D1.1 validator doesn't require a report block.
    v = AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x", is_no_data=True)
    assert v.key_factors == []


def test_verdict_rejects_bad_lean():
    with pytest.raises(ValidationError):
        _verdict(lean="up")


def test_verdict_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        _verdict(confidence=1.5)


def test_verdict_rejects_magnitude_out_of_range():
    with pytest.raises(ValidationError):
        _verdict(magnitude=1.5)


# The previous regression here ("verdict rejects rationale over schema cap")
# was removed when ``AnalystVerdict.rationale`` lost its ``max_length``
# constraint as part of the 2026-05-25 schema-split fix.  Vertex's
# constrained decoder treats schema ``maxLength`` as a fill target and pads
# strings toward the cap (verbatim repetition, hallucinated padding) — the
# cap is now stated in the prompt only.  The LLM analysts no longer emit
# ``rationale`` at all; deterministic extractors that still populate it are
# trusted to honour the prose budget.


def test_evidence_valid():
    e = AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        tick_id="2026-05-08T14:00:00Z",
        recorded_at=_now(),
        features={"rsi_14": 42.3, "pct_change_5d": -0.018},
        verdict=_verdict(lean="bearish", magnitude=0.4, confidence=0.6, rationale="weakening"),
    )
    assert e.ticker == "AAPL"
    assert e.analyst == "technical"
    assert e.tick_id == "2026-05-08T14:00:00Z"
    assert e.features["rsi_14"] == 42.3


def test_evidence_rejects_unknown_analyst():
    with pytest.raises(ValidationError):
        AnalystEvidence(
            ticker="AAPL",
            analyst="macro",
            tick_id="t",
            recorded_at=_now(),
            features={},
            verdict=_verdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x"),
        )


def test_evidence_round_trip():
    original = AnalystEvidence(
        ticker="MSFT",
        analyst="fundamental",
        tick_id="2026-05-08T15:00:00Z",
        recorded_at=_now(),
        features={"pe_trailing": 32.5, "fcf_yield_pct": 2.4},
        verdict=_verdict(lean="neutral", magnitude=0.1, confidence=0.4,
                         rationale="balanced", key_factors=["pe_trailing: 32.5"]),
    )
    dumped = original.model_dump(mode="json")
    rebuilt = AnalystEvidence.model_validate(dumped)
    assert rebuilt == original


# ---------------------------------------------------------------------------
# D6 content assertions: is_no_data, ticker key, populated fields
# ---------------------------------------------------------------------------


def test_verdict_is_no_data_false_for_normal_verdict():
    """A real (non-no-data) verdict must have ``is_no_data`` explicitly False.

    This is the per-ticker guard against silent degradation: an evidence
    object whose ``is_no_data`` was accidentally set True would be treated
    as missing data by the strategist, silently dropping a real signal.
    The assertion must be on the field value, not just on construction.
    """
    v = _verdict(lean="bullish", magnitude=0.7, confidence=0.8,
                 rationale="strong momentum", is_no_data=False)

    # Content assertion: is_no_data must be the concrete boolean False,
    # not None or a truthy string.
    assert v.is_no_data is False, (
        "A real verdict must carry is_no_data=False — a True value "
        "would cause the strategist to silently skip the signal"
    )
    # Sanity: a real verdict must also carry a non-empty prose surface.
    assert v.rationale, "a non-no-data verdict must have a non-empty rationale"


def test_evidence_ticker_key_is_accessible_and_correct():
    """The ``ticker`` field on ``AnalystEvidence`` must carry the exact symbol
    passed at construction — no normalisation, case-folding, or truncation.

    This guards against an extractor bug where ``evidence.ticker`` is set to
    a different symbol than the one the pipeline requested (e.g. because a
    bulk-fetch result was keyed differently).
    """
    e = AnalystEvidence(
        ticker="NVDA",
        analyst="social",
        tick_id="2026-06-10T09:30:00Z",
        recorded_at=_now(),
        features={"sentiment_score": 0.72, "mention_velocity_pct": 14.0},
        verdict=_verdict(lean="bullish", magnitude=0.6, confidence=0.75,
                         rationale="options flow bullish"),
    )

    # Content assertion: ticker must be the exact input string.
    assert e.ticker == "NVDA", (
        f"evidence.ticker must be 'NVDA'; got {e.ticker!r} — "
        "a mismatch would silently attribute the wrong signal to the wrong ticker"
    )

    # The features dict must be present and non-empty for a real verdict.
    assert "sentiment_score" in e.features, (
        "features dict must contain 'sentiment_score'"
    )
    assert e.features["sentiment_score"] == 0.72, (
        f"feature value must be exact; got {e.features['sentiment_score']}"
    )

    # is_no_data must be False for a real data verdict.
    assert e.verdict.is_no_data is False, (
        "verdict.is_no_data must be False for a properly-evidenced ticker"
    )


def test_build_no_data_verdict_carries_ticker_and_reason():
    """``build_no_data_verdict`` must produce a ``TickerVerdict`` whose
    ``ticker`` and ``rationale`` fields carry the supplied values.

    This is a content assertion on the canonical no-data constructor — an
    empty rationale or wrong ticker would silently misattribute the no-data
    signal to the wrong symbol or leave the strategist without a reason.
    """
    from contract.evidence import build_no_data_verdict

    v = build_no_data_verdict("AMZN", reason="no filings this quarter")

    # Content assertions: ticker and reason must be present and exact.
    assert v.ticker == "AMZN", (
        f"no-data verdict ticker must be 'AMZN'; got {v.ticker!r}"
    )
    assert "no filings" in v.rationale, (
        f"rationale must carry the supplied reason; got {v.rationale!r}"
    )
    assert v.is_no_data is True, (
        "build_no_data_verdict must produce is_no_data=True"
    )


def test_build_no_data_verdict_rejects_empty_reason():
    """``build_no_data_verdict`` must raise ``ValueError`` when called with an
    empty-or-whitespace reason string.

    The no-data builder closes the silent-fallback bug class: every no-data
    site already has a concrete reason available.  An empty reason must be
    rejected loudly so the caller is forced to provide one.
    """
    from contract.evidence import build_no_data_verdict

    with pytest.raises(ValueError, match="non-empty reason"):
        build_no_data_verdict("TSLA", reason="")
