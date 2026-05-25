"""Narrow-emit schema contract for the News + Fundamental LLM analysts.

``LlmTickerVerdict`` is the schema that the per-ticker LlmAgent's
``output_schema`` points at; the joiner inflates each emit into the
canonical downstream ``TickerVerdict`` for the rest of the pipeline.

The class was introduced after the 2026-05-25 schema-failure audit on
``baseline-2025-09 / post-mem-test-5`` — the dominant failure mode was
Vertex's constrained decoder taking the "shortest legal path" through
the old emit-schema and silently omitting ``is_no_data`` + ``report``.
These tests pin the three structural fixes that closed that gap:

1. ``is_no_data`` and ``report`` are required at the JSON-Schema level
   (no defaults, no Optional) — the decoder can no longer omit them.
2. ``extra="forbid"`` — drift between this class and a stale prompt
   fails loudly rather than silently dropping fields.  In particular,
   the ``rationale`` field was dropped from the LLM emit (the prose
   surface now lives on ``report.summary``); attempting to emit it
   must raise.
3. ``ticker`` must be non-empty — an empty string would silently break
   the joiner's per-ticker indexing.

The downstream-shape compatibility check (``TickerVerdict`` still
accepts a payload without ``rationale``, defaulting to ``""``) is also
covered here so the joiner inflation path is exercised end-to-end at
the contract boundary.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import (
    AnalystReport,
    LlmTickerVerdict,
    ReportDriver,
    TickerVerdict,
)


def _valid_report() -> AnalystReport:
    """Build a minimal valid ``AnalystReport`` for use in the LLM emit payload.

    Two drivers is the schema minimum; one bull + one bear keeps the test
    payload directionally neutral so the schema assertions are independent
    of the lean/magnitude/confidence values used by the caller.
    """

    return AnalystReport(
        summary="Test summary — exercises the LlmTickerVerdict contract.",
        drivers=[
            ReportDriver(name="driver-one", direction="bull",
                         weight=0.6, body="Body one."),
            ReportDriver(name="driver-two", direction="bear",
                         weight=0.4, body="Body two."),
        ],
    )


def _valid_emit_payload(**overrides: object) -> dict[str, object]:
    """Build a fully-valid ``LlmTickerVerdict`` payload, with optional overrides.

    Per-test overrides are merged on top of the canonical baseline so each
    test states only the field it is exercising; everything else is the
    happy-path default.
    """

    payload: dict[str, object] = {
        "ticker":      "AAPL",
        "lean":        "bullish",
        "magnitude":   0.5,
        "confidence":  0.6,
        "is_no_data":  False,
        "key_factors": ["catalyst:earnings_beat"],
        "report":      _valid_report().model_dump(),
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Required-field enforcement
# ---------------------------------------------------------------------------


def test_is_no_data_is_required() -> None:
    """Omitting ``is_no_data`` must raise — it has no default on the LLM emit."""

    payload = _valid_emit_payload()
    payload.pop("is_no_data")

    with pytest.raises(ValidationError) as excinfo:
        LlmTickerVerdict.model_validate(payload)

    # Pydantic reports the missing-field path; assert on the field name so
    # the test stays robust against minor wording changes.
    assert "is_no_data" in str(excinfo.value)


def test_report_is_required() -> None:
    """Omitting ``report`` must raise — it has no default and is not Optional."""

    payload = _valid_emit_payload()
    payload.pop("report")

    with pytest.raises(ValidationError) as excinfo:
        LlmTickerVerdict.model_validate(payload)

    assert "report" in str(excinfo.value)


def test_report_cannot_be_none() -> None:
    """An explicit ``report=None`` must raise — the field is not Optional."""

    with pytest.raises(ValidationError):
        LlmTickerVerdict.model_validate(_valid_emit_payload(report=None))


# ---------------------------------------------------------------------------
# Extra-field rejection (extra="forbid")
# ---------------------------------------------------------------------------


def test_extra_field_rationale_is_forbidden() -> None:
    """The dropped ``rationale`` field must be rejected, not silently ignored.

    The LLM prompt was updated to stop instructing the model to emit
    ``rationale`` (the prose surface now lives on ``report.summary``).  If a
    stale prompt or a confused model emits the old field anyway, we want a
    loud schema failure that the retry layer can handle — not a silent drop
    that would mask the prompt/schema drift.
    """

    with pytest.raises(ValidationError) as excinfo:
        LlmTickerVerdict.model_validate(
            _valid_emit_payload(rationale="should not be emitted"),
        )

    # ``extra="forbid"`` surfaces the offending field name in the error.
    assert "rationale" in str(excinfo.value)


def test_arbitrary_extra_field_is_forbidden() -> None:
    """Any other unknown field is also rejected — defence-in-depth on drift."""

    with pytest.raises(ValidationError):
        LlmTickerVerdict.model_validate(
            _valid_emit_payload(some_new_field="x"),
        )


# ---------------------------------------------------------------------------
# Ticker validator
# ---------------------------------------------------------------------------


def test_empty_ticker_is_rejected() -> None:
    """An empty ticker string would silently break joiner indexing — reject it."""

    with pytest.raises(ValidationError) as excinfo:
        LlmTickerVerdict.model_validate(_valid_emit_payload(ticker=""))

    assert "ticker" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Happy-path round-trip
# ---------------------------------------------------------------------------


def test_valid_emit_round_trips() -> None:
    """A fully-populated valid payload validates cleanly and preserves fields."""

    v = LlmTickerVerdict.model_validate(_valid_emit_payload())

    assert v.ticker      == "AAPL"
    assert v.lean        == "bullish"
    assert v.is_no_data is False
    assert v.report.summary.startswith("Test summary")
    assert v.report.drivers[0].direction == "bull"


def test_is_no_data_true_still_requires_report() -> None:
    """Even when ``is_no_data=True``, ``report`` must be supplied on the LLM emit.

    This is the core difference from ``AnalystVerdict``, which allows
    ``report=None`` in the no-data case.  Forcing the LLM to always emit a
    report (even a one-line "no data" summary) eliminates the optional
    branch that Vertex's decoder was taking to short-circuit the emit.
    """

    v = LlmTickerVerdict.model_validate(_valid_emit_payload(is_no_data=True))

    assert v.is_no_data is True
    assert v.report is not None


# ---------------------------------------------------------------------------
# Downstream inflation path
# ---------------------------------------------------------------------------


def test_llm_emit_inflates_into_downstream_ticker_verdict() -> None:
    """A dumped ``LlmTickerVerdict`` must validate as a ``TickerVerdict``.

    The joiner round-trips emits through ``model_dump`` → ``model_validate``
    on the downstream class, relying on ``rationale`` defaulting to ``""``
    so the absence of the field on the LLM side does not break the inflation.
    """

    emit = LlmTickerVerdict.model_validate(_valid_emit_payload())

    downstream = TickerVerdict.model_validate(emit.model_dump())

    assert downstream.ticker    == "AAPL"
    assert downstream.rationale == ""             # default — LLM no longer emits
    assert downstream.report is not None
    assert downstream.report.summary.startswith("Test summary")


def test_analyst_verdict_accepts_payload_without_rationale() -> None:
    """``AnalystVerdict`` (downstream parent of ``TickerVerdict``) defaults rationale.

    Pins the contract the LLM inflation path depends on — without this
    default the joiner would have to inject ``rationale=""`` itself, and a
    future "tighten the schema" refactor that removed the default would
    silently break LLM inflation.
    """

    from contract.evidence import AnalystVerdict

    v = AnalystVerdict.model_validate(
        {
            "lean":        "neutral",
            "magnitude":   0.0,
            "confidence":  0.0,
            "key_factors": [],
            "is_no_data":  True,
            "report":      None,
        }
    )

    assert v.rationale == ""
