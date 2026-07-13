"""Tests for the self-relative filing-similarity scale summariser."""
from __future__ import annotations

from agents.analysts.fundamental.scale_summary import build_scale_summary


def _summary(current: float, history: list[float]) -> str:
    return build_scale_summary(
        section_label="MD&A", form_type="10-Q",
        current_cosine=current, current_jaccard=0.7,
        history_cosines=history,
        high_pct=0.80, low_pct=0.20, min_history=3,
    )


def test_changed_more_than_usual_is_flagged_bottom() -> None:
    """A cosine below the firm's own history reads as 'changed more than usual'."""
    text = _summary(0.50, [0.90, 0.92, 0.88, 0.91, 0.89])
    assert "more than usual" in text.lower()
    assert "10-Q MD&A" in text


def test_changed_less_than_usual_is_flagged_top() -> None:
    """A cosine above the firm's own history reads as 'changed less than usual'."""
    text = _summary(0.97, [0.70, 0.72, 0.68, 0.75, 0.71])
    assert "less than usual" in text.lower()


def test_thin_history_hedges_instead_of_banding() -> None:
    """With too few prior points, hedge honestly — no false-precision band."""
    text = _summary(0.80, [0.75])
    assert "limited" in text.lower() or "only" in text.lower()
    assert "percentile" not in text.lower()
