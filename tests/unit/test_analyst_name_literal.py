"""The AnalystName Literal must include 'news' + 'social' and exclude 'sentiment'."""
from __future__ import annotations

from typing import get_args

from contract.evidence import AnalystName


def test_analyst_name_includes_news_and_social() -> None:
    """Post-Phase-5: 'news' and 'social' are first-class analyst names."""
    members = set(get_args(AnalystName))
    assert "news" in members
    assert "social" in members


def test_analyst_name_excludes_sentiment() -> None:
    """Post-Phase-5: 'sentiment' no longer exists as an analyst name."""
    members = set(get_args(AnalystName))
    assert "sentiment" not in members


def test_analyst_name_full_membership() -> None:
    """The full set is exactly the five Phase-5 analysts."""
    members = set(get_args(AnalystName))
    assert members == {"technical", "fundamental", "news", "social", "smart_money"}
