"""Unit tests for :func:`agents.thinking_config.build_thinking_config`.

The helper is the single place that turns a config-declared thinking knob into
a Gemini ``ThinkingConfig``, selecting between the two *mutually exclusive*
forms (``thinking_budget`` for Gemini 2.5, ``thinking_level`` for Gemini 3) and
failing loudly when both are set — which is exactly the request shape Gemini 3
rejects with an HTTP 400.
"""
from __future__ import annotations

import pytest
from google.genai import types as genai_types

from agents.thinking_config import build_thinking_config


class TestBuildThinkingConfig:
    """Knob-selection, pass-through and loud-failure behaviour."""

    def test_thinking_level_only_builds_level_config(self) -> None:
        """A configured level (Gemini 3 form) yields a level-only config."""

        cfg = build_thinking_config(thinking_budget=None, thinking_level="medium")

        assert cfg is not None
        assert cfg.thinking_level == genai_types.ThinkingLevel.MEDIUM
        assert cfg.thinking_budget is None

    def test_thinking_level_is_case_insensitive(self) -> None:
        """The level is upper-cased to match the SDK enum, so case is free."""

        cfg = build_thinking_config(thinking_budget=None, thinking_level="High")

        assert cfg is not None
        assert cfg.thinking_level == genai_types.ThinkingLevel.HIGH

    def test_thinking_budget_only_builds_budget_config(self) -> None:
        """A configured budget (Gemini 2.5 form) yields a budget-only config."""

        cfg = build_thinking_config(thinking_budget=2048, thinking_level=None)

        assert cfg is not None
        assert cfg.thinking_budget == 2048
        assert cfg.thinking_level is None

    def test_zero_budget_is_passed_through_not_treated_as_unset(self) -> None:
        """``0`` disables thinking and must reach the API, not collapse to None."""

        cfg = build_thinking_config(thinking_budget=0, thinking_level=None)

        assert cfg is not None
        assert cfg.thinking_budget == 0

    def test_neither_returns_none(self) -> None:
        """No knob → ``None`` (native thinking, identical to omitting it)."""

        assert build_thinking_config(thinking_budget=None, thinking_level=None) is None

    def test_both_set_raises(self) -> None:
        """Both knobs together is the Gemini 3 400 — refuse before the call."""

        with pytest.raises(ValueError, match="mutually exclusive"):
            build_thinking_config(thinking_budget=2048, thinking_level="medium")

    def test_unknown_level_raises(self) -> None:
        """An unrecognised level is a loud failure, not a silent fallback."""

        with pytest.raises(ValueError):
            build_thinking_config(thinking_budget=None, thinking_level="ultra")
