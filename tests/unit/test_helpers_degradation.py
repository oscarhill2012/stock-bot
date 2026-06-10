"""Self-tests for tests/_helpers/degradation.py."""
from __future__ import annotations

import logging

import pytest

from tests._helpers import assert_no_silent_degradation


def test_passes_on_clean_state(caplog):
    """A state with all is_no_data=False and no warnings passes."""
    caplog.set_level(logging.WARNING)
    state = {
        "news_verdicts": [{"ticker": "AAPL", "is_no_data": False}],
        "news_evidence": [{"ticker": "AAPL", "verdict": {"is_no_data": False}}],
    }
    assert_no_silent_degradation(state)


def test_fails_on_silent_no_data(caplog):
    """Any verdict with is_no_data=True triggers an AssertionError."""
    caplog.set_level(logging.WARNING)
    state = {"news_verdicts": [{"ticker": "AAPL", "is_no_data": True}]}
    with pytest.raises(AssertionError, match="is_no_data=True"):
        assert_no_silent_degradation(state)


def test_allow_degradation_suppresses_named_domain(caplog):
    """A domain named in allow_degradation may carry is_no_data=True."""
    caplog.set_level(logging.WARNING)
    state = {"news_verdicts": [{"ticker": "AAPL", "is_no_data": True}]}
    assert_no_silent_degradation(state, allow_degradation=("news",))


def test_fails_on_branch_failed_log(caplog):
    """A WARNING record containing 'branch_failed' fails the assertion."""
    caplog.set_level(logging.WARNING)
    logging.getLogger("test").warning("branch_failed: news fetch died")
    state = {"news_verdicts": [{"ticker": "AAPL", "is_no_data": False}]}
    with pytest.raises(AssertionError, match="branch_failed"):
        assert_no_silent_degradation(state)
