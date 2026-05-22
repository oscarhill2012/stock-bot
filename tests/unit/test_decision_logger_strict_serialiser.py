"""S5 — strict decision-logger serialiser + insider .model_dump() at fetch."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.decision_logger import _serialise_snapshot


class _NotJsonable:
    """A class with no JSON-serialisable representation."""

    def __repr__(self) -> str:
        return "<not-jsonable>"


def test_serialiser_handles_nested_pydantic_models() -> None:
    """Pydantic models nested inside lists/dicts round-trip as dicts."""

    from contract.evidence import AnalystVerdict

    nested = {
        "verdict": AnalystVerdict.model_validate(
            {
                "lean":        "neutral",
                "magnitude":   0.0,
                "confidence":  0.0,
                "rationale":   "no data",
                "key_factors": [],
                "is_no_data":  True,
                "report":      None,
            }
        ),
        "list_of_verdicts": [
            AnalystVerdict.model_validate(
                {
                    "lean":        "neutral",
                    "magnitude":   0.0,
                    "confidence":  0.0,
                    "rationale":   "no data",
                    "key_factors": [],
                    "is_no_data":  True,
                    "report":      None,
                }
            ),
        ],
    }

    out = _serialise_snapshot(nested)
    parsed = json.loads(out)

    assert isinstance(parsed["verdict"], dict)
    assert parsed["verdict"]["lean"] == "neutral"
    assert isinstance(parsed["list_of_verdicts"], list)
    assert isinstance(parsed["list_of_verdicts"][0], dict)


def test_serialiser_raises_on_unjsonable() -> None:
    """An un-dumpable type must raise, not silently emit a repr string."""

    with pytest.raises(TypeError):
        _serialise_snapshot({"bad": _NotJsonable()})
