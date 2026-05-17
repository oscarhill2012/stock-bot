"""Unit tests for the ``AnalystRating`` and ``AnalystRevision`` models."""
from __future__ import annotations

from datetime import date

from data.models.analyst_consensus import AnalystRating, AnalystRevision


def test_analyst_rating_minimal() -> None:
    """AnalystRating can be constructed with only required fields."""
    r = AnalystRating(ticker="AAPL", as_of=date(2023, 3, 10))
    assert r.target_mean is None
    assert r.number_of_analysts is None


def test_analyst_rating_fully_populated() -> None:
    """AnalystRating accepts all optional fields when supplied."""
    r = AnalystRating(
        ticker="AAPL", as_of=date(2023, 3, 10),
        target_high=210.0, target_low=140.0,
        target_mean=175.0, target_median=178.0,
        recommendation_mean=2.1, number_of_analysts=42,
    )
    assert r.target_mean == 175.0
    assert r.number_of_analysts == 42


def test_analyst_revision_action_literal() -> None:
    """AnalystRevision accepts valid action literals."""
    r = AnalystRevision(
        ticker="AAPL", firm="GS", action="upgrade",
        from_grade="Neutral", to_grade="Buy",
        event_date=date(2023, 3, 10),
    )
    assert r.action == "upgrade"
    assert r.from_grade == "Neutral"


def test_analyst_revision_all_action_literals() -> None:
    """All seven valid action values are accepted without error."""
    valid_actions = [
        "upgrade", "downgrade", "initiate",
        "reiterate", "target_raise", "target_cut", "unknown",
    ]
    for action in valid_actions:
        rev = AnalystRevision(
            ticker="AAPL", firm="MS", action=action,
            event_date=date(2023, 3, 10),
        )
        assert rev.action == action


def test_analyst_rating_round_trip() -> None:
    """AnalystRating survives model_dump → model_validate round-trip."""
    r = AnalystRating(
        ticker="TSLA", as_of=date(2023, 3, 10),
        target_mean=200.0, number_of_analysts=30,
    )
    restored = AnalystRating.model_validate(r.model_dump())
    assert restored == r


def test_analyst_revision_optional_grades_default_none() -> None:
    """from_grade and to_grade default to None."""
    r = AnalystRevision(
        ticker="AAPL", firm="JPM", action="initiate",
        event_date=date(2023, 3, 10),
    )
    assert r.from_grade is None
    assert r.to_grade is None
