"""Tests for the after-model clamp callback on the Strategist agent.

The Strategist's ``output_schema`` (``StrategistDecision``) enforces
``preferred_weight`` and ``conviction`` to ``[0.0, 1.0]`` via Pydantic
``Field(ge=0.0, le=1.0)``.  Gemini occasionally drifts and emits negative
values (typically to express a short / bearish stance), which then triggers
a ``ValidationError`` inside ADK's ``_maybe_save_output_to_state`` and aborts
the whole tick.

``_clamp_stance_bounds_after_model`` is the safety net: it fires after the
LLM call but before ADK's schema validation, deserialises the JSON payload,
clamps any out-of-range numerics on each stance, and rewrites the response
text.  These tests exercise the canonical drift cases plus a handful of
defensive paths.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from agents.strategist.agent import (
    _CLAMPED_STANCE_FIELDS,
    _clamp_stance_bounds_after_model,
)

# ── Test scaffolding ─────────────────────────────────────────────────────────


def _make_response(payload_text: str | None) -> SimpleNamespace:
    """Build a duck-typed ``LlmResponse`` carrying a single text part.

    Mirrors the minimum shape the clamp callback reads:
    ``response.content.parts[0].text``.  Using ``SimpleNamespace`` keeps the
    test independent of ADK's concrete response classes (they change
    occasionally and the callback only needs duck-typed access).

    Parameters
    ----------
    payload_text:
        The JSON text to place on ``parts[0].text``.  ``None`` means "no
        text attribute set" — used to exercise the empty-response guard.

    Returns
    -------
    SimpleNamespace
        A mock LlmResponse with one text part.
    """
    part = SimpleNamespace(text=payload_text)
    content = SimpleNamespace(parts=[part])
    return SimpleNamespace(content=content)


def _decision_payload(stances: list[dict]) -> dict:
    """Build a minimal decision payload around the supplied stance dicts.

    The clamp callback only walks ``payload["stances"]`` — the other fields
    exist purely to keep the shape realistic so we know the callback
    preserves them.

    Parameters
    ----------
    stances:
        Pre-built stance dicts to nest under ``"stances"``.

    Returns
    -------
    dict
        A decision-shaped dict ready to ``json.dumps``.
    """
    return {
        "stances":        stances,
        "decision_tag":   "test",
        "reasoning":      "test reasoning",
        "updated_thesis": "test thesis",
        "confidence":     0.5,
    }


def _ctx() -> object:
    """Return a placeholder callback context.

    The clamp callback ignores its first argument (it only reads the
    response), so any non-``None`` object will do.
    """
    return SimpleNamespace(state={})


# ── Core clamping behaviour ──────────────────────────────────────────────────


def test_clamps_negative_preferred_weight_to_zero(caplog):
    """A negative ``preferred_weight`` (the canonical Gemini drift) is clamped to 0.0."""
    payload = _decision_payload([
        {"ticker": "AAPL", "preferred_weight": -0.08, "conviction": 0.6, "rationale": "x"},
    ])
    response = _make_response(json.dumps(payload))

    with caplog.at_level(logging.WARNING, logger="agents.strategist.agent"):
        result = _clamp_stance_bounds_after_model(_ctx(), response)

    # Callback always returns None so ADK proceeds with the mutated response.
    assert result is None

    mutated = json.loads(response.content.parts[0].text)
    assert mutated["stances"][0]["preferred_weight"] == 0.0
    # Untouched field stays as-is (proves we did not over-mutate).
    assert mutated["stances"][0]["conviction"] == 0.6

    # The WARNING line must mention the ticker AND field so on-call can find it.
    assert any("AAPL" in rec.message and "preferred_weight" in rec.message
               for rec in caplog.records)


def test_clamps_value_above_one_down_to_one():
    """Values above 1.0 (less common but possible) are clamped down to 1.0."""
    payload = _decision_payload([
        {"ticker": "MSFT", "preferred_weight": 1.4, "conviction": 0.7, "rationale": "x"},
    ])
    response = _make_response(json.dumps(payload))

    _clamp_stance_bounds_after_model(_ctx(), response)

    mutated = json.loads(response.content.parts[0].text)
    assert mutated["stances"][0]["preferred_weight"] == 1.0


def test_clamps_negative_conviction_to_zero():
    """``conviction`` also carries ``ge=0.0``; verify it is clamped too."""
    payload = _decision_payload([
        {"ticker": "GOOG", "preferred_weight": 0.10, "conviction": -0.3, "rationale": "x"},
    ])
    response = _make_response(json.dumps(payload))

    _clamp_stance_bounds_after_model(_ctx(), response)

    mutated = json.loads(response.content.parts[0].text)
    assert mutated["stances"][0]["conviction"] == 0.0
    # Preferred weight, which was already valid, must not be touched.
    assert mutated["stances"][0]["preferred_weight"] == 0.10


def test_clamps_multiple_stances_in_a_single_pass(caplog):
    """A realistic Gemini drift hits several tickers — all get clamped, one log line."""
    payload = _decision_payload([
        {"ticker": "AAPL", "preferred_weight": -0.05, "conviction": 0.6, "rationale": "x"},
        {"ticker": "MSFT", "preferred_weight":  0.10, "conviction": 0.7, "rationale": "x"},  # ok
        {"ticker": "NVDA", "preferred_weight": -0.12, "conviction": 1.5, "rationale": "x"},
        {"ticker": "AMZN", "preferred_weight":  0.50, "conviction": 0.4, "rationale": "x"},  # ok
    ])
    response = _make_response(json.dumps(payload))

    with caplog.at_level(logging.WARNING, logger="agents.strategist.agent"):
        _clamp_stance_bounds_after_model(_ctx(), response)

    mutated = json.loads(response.content.parts[0].text)
    weights = {s["ticker"]: s["preferred_weight"] for s in mutated["stances"]}
    convictions = {s["ticker"]: s["conviction"] for s in mutated["stances"]}

    assert weights == {"AAPL": 0.0, "MSFT": 0.10, "NVDA": 0.0, "AMZN": 0.50}
    assert convictions == {"AAPL": 0.6, "MSFT": 0.7, "NVDA": 1.0, "AMZN": 0.4}

    # A single warning that names every clamp keeps log volume manageable.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "AAPL" in msg and "NVDA" in msg
    # The two in-range stances must not appear in the clamp log.
    assert "MSFT" not in msg
    assert "AMZN" not in msg


# ── No-op paths ──────────────────────────────────────────────────────────────


def test_no_op_when_all_values_in_bounds(caplog):
    """Clean input must not mutate the response and must not log."""
    payload = _decision_payload([
        {"ticker": "AAPL", "preferred_weight": 0.05, "conviction": 0.6, "rationale": "x"},
        {"ticker": "MSFT", "preferred_weight": 0.0,  "conviction": 1.0, "rationale": "x"},
    ])
    original_text = json.dumps(payload)
    response = _make_response(original_text)

    with caplog.at_level(logging.WARNING, logger="agents.strategist.agent"):
        _clamp_stance_bounds_after_model(_ctx(), response)

    # Text must be byte-identical (no re-serialisation, no spurious warning).
    assert response.content.parts[0].text == original_text
    assert not any(r.levelno == logging.WARNING for r in caplog.records)


def test_boundaries_zero_and_one_are_not_clamped():
    """0.0 and 1.0 are valid edge values — must pass through untouched."""
    payload = _decision_payload([
        {"ticker": "AAPL", "preferred_weight": 0.0, "conviction": 1.0, "rationale": "x"},
    ])
    original_text = json.dumps(payload)
    response = _make_response(original_text)

    _clamp_stance_bounds_after_model(_ctx(), response)

    # No mutation expected at exact bounds.
    assert response.content.parts[0].text == original_text


# ── Defensive paths — must never raise on malformed input ────────────────────


def test_tolerates_garbage_json():
    """Non-JSON text on the response is left alone for the downstream parser."""
    response = _make_response("this is not json {{{")

    # Must not raise; downstream validator will surface the real error.
    result = _clamp_stance_bounds_after_model(_ctx(), response)

    assert result is None
    assert response.content.parts[0].text == "this is not json {{{"


def test_tolerates_missing_content():
    """A response with no ``content`` attribute is silently ignored."""
    response = SimpleNamespace(content=None)
    result = _clamp_stance_bounds_after_model(_ctx(), response)
    assert result is None


def test_tolerates_empty_parts_list():
    """A response with ``content.parts == []`` is silently ignored."""
    response = SimpleNamespace(content=SimpleNamespace(parts=[]))
    result = _clamp_stance_bounds_after_model(_ctx(), response)
    assert result is None


def test_tolerates_part_with_no_text():
    """A part whose ``text`` is ``None`` is silently ignored."""
    response = _make_response(None)
    result = _clamp_stance_bounds_after_model(_ctx(), response)
    assert result is None


def test_tolerates_non_dict_payload():
    """A JSON payload that isn't an object (e.g. a bare list) is ignored cleanly."""
    response = _make_response(json.dumps(["not", "a", "decision"]))
    result = _clamp_stance_bounds_after_model(_ctx(), response)
    assert result is None


def test_tolerates_non_list_stances_field():
    """``stances`` present but not a list (string / dict / null) is ignored cleanly."""
    response = _make_response(json.dumps({"stances": "oops"}))
    result = _clamp_stance_bounds_after_model(_ctx(), response)
    assert result is None


def test_skips_non_dict_stance_entries():
    """Inside ``stances`` we tolerate non-dict items rather than crashing.

    The downstream Pydantic validator will reject these — the clamp callback's
    job is not to enforce shape, only to clamp numerics where present.
    """
    payload = {
        "stances": [
            "not-a-dict",
            {"ticker": "AAPL", "preferred_weight": -0.1, "conviction": 0.5, "rationale": "x"},
            42,
        ],
        "decision_tag": "t", "reasoning": "x", "updated_thesis": "y", "confidence": 0.5,
    }
    response = _make_response(json.dumps(payload))

    _clamp_stance_bounds_after_model(_ctx(), response)

    mutated = json.loads(response.content.parts[0].text)
    # The dict stance was clamped; the junk entries were preserved as-is.
    assert mutated["stances"][0] == "not-a-dict"
    assert mutated["stances"][1]["preferred_weight"] == 0.0
    assert mutated["stances"][2] == 42


def test_ignores_non_numeric_values_and_booleans():
    """A bool or string in a clamped field is left for Pydantic to reject.

    ``bool`` is a subclass of ``int`` in Python, so without the explicit
    guard ``True`` would be treated as ``1`` and ``False`` as ``0`` —
    silently masking a malformed response.  The schema would catch it
    later, but we'd rather not lie in the trace.
    """
    payload = _decision_payload([
        {"ticker": "AAPL", "preferred_weight": True,    "conviction": "0.5", "rationale": "x"},
        {"ticker": "MSFT", "preferred_weight": None,    "conviction": False, "rationale": "x"},
    ])
    response = _make_response(json.dumps(payload))

    _clamp_stance_bounds_after_model(_ctx(), response)

    mutated = json.loads(response.content.parts[0].text)
    # Nothing should have been touched — every value here is non-numeric or bool.
    assert mutated["stances"][0]["preferred_weight"] is True
    assert mutated["stances"][0]["conviction"] == "0.5"
    assert mutated["stances"][1]["preferred_weight"] is None
    assert mutated["stances"][1]["conviction"] is False


# ── Sanity guard on the module constant ──────────────────────────────────────


def test_clamped_fields_constant_matches_schema_constraints():
    """The constant must list exactly the schema fields that carry ``[0, 1]``.

    If a future field gains the same ``ge=0.0, le=1.0`` constraint and is
    not added here, the clamp will silently miss it.  Conversely, listing a
    field that no longer carries the constraint would over-clamp valid
    output.  Pin both ends with the schema as the source of truth.
    """
    from agents.strategist.stance_schema import TickerStance

    expected: set[str] = set()
    for field_name, field_info in TickerStance.model_fields.items():
        ge = None
        le = None
        for meta in field_info.metadata:
            ge = getattr(meta, "ge", ge)
            le = getattr(meta, "le", le)
        if ge == 0.0 and le == 1.0:
            expected.add(field_name)

    assert set(_CLAMPED_STANCE_FIELDS) == expected, (
        f"_CLAMPED_STANCE_FIELDS drifted from schema: "
        f"constant={set(_CLAMPED_STANCE_FIELDS)} schema_fields={expected}"
    )
