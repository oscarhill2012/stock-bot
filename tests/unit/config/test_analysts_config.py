"""Unit tests for the analysts.json config loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.analysts import AnalystsConfig, get_analysts_config, load_analysts_config

# ---------------------------------------------------------------------------
# Shared minimal-config payload
# ---------------------------------------------------------------------------
# Each test below either uses this payload as-is or overrides one field.  The
# ``output_caps`` block is required by the schema (no defaults at the field
# level), so every fixture must carry it.
# ---------------------------------------------------------------------------

_MINIMAL_LLM_CAPS: dict = {
    "timeout_seconds":   60,
    "max_output_tokens": 2000,
    "temperature":       0.3,
    "timeout_retries":   3,
    "schema_retries":    3,
}

_MINIMAL_CFG: dict = {
    "news": {
        "max_articles_per_ticker": 20,
        "max_stale_headlines_per_ticker": 5,
        "max_summary_chars":       500,
        "llm":                     _MINIMAL_LLM_CAPS,
    },
    "fundamental": {
        "max_filing_mda_chars":       1500,
        "max_filing_risk_chars":      1500,
        "max_filing_8k_body_chars":   1500,
        "max_insider_footnotes":         5,
        "max_insider_footnote_chars":  400,
        "llm":                         _MINIMAL_LLM_CAPS,
    },
    "output_caps": {
        "verdict_rationale_max_chars":   160,
        "report_summary_max_chars":     2000,
        "report_driver_name_max_chars":   60,
        "report_driver_body_max_chars": 1000,
    },
    "cache": {"enabled": True, "directory": "cache/reports"},
}


def test_load_analysts_config_default_values(tmp_path: Path) -> None:
    """A minimal config file populates fields with the documented defaults."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(_MINIMAL_CFG))

    cfg = load_analysts_config(path=cfg_file)
    assert isinstance(cfg, AnalystsConfig)
    assert cfg.news.max_articles_per_ticker == 20
    assert cfg.fundamental.max_filing_mda_chars == 1500
    assert cfg.cache.enabled is True
    assert cfg.cache.directory == "cache/reports"
    # slack_percent defaults to 10 when omitted from the JSON file.
    assert cfg.slack_percent == 10
    assert cfg.output_caps.verdict_rationale_max_chars == 160


def test_load_analysts_config_rejects_negative_caps(tmp_path: Path) -> None:
    """Negative truncation caps must fail validation — they are sentinel-poisoning."""
    payload = {
        **_MINIMAL_CFG,
        "news": {"max_articles_per_ticker": -1, "max_summary_chars": 500, "llm": _MINIMAL_LLM_CAPS},
    }
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_rejects_oversized_caps(tmp_path: Path) -> None:
    """An operator-error oversized cap (e.g. max_articles_per_ticker=9999) must
    fail validation — the upper bound exists for a reason."""
    payload = {
        **_MINIMAL_CFG,
        "news": {"max_articles_per_ticker": 9999, "max_summary_chars": 500, "llm": _MINIMAL_LLM_CAPS},
    }
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_schema_cap_applies_slack_percent(tmp_path: Path) -> None:
    """``AnalystsConfig.schema_cap`` should add ``slack_percent`` headroom using
    integer math.  Verifies both a value that floats cleanly (600 → 660) and
    one that doesn't (200 → 220) to pin the FP-rounding fix in place.
    """
    payload = {**_MINIMAL_CFG, "slack_percent": 10}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    cfg = load_analysts_config(path=cfg_file)
    assert cfg.schema_cap(200) == 220      # 200 * 1.10 — FP would give 220.00000…003
    assert cfg.schema_cap(600) == 660      # 600 * 1.10 — exact in FP
    assert cfg.schema_cap(160) == 176      # 160 * 1.10


def test_schema_cap_with_zero_slack_is_identity(tmp_path: Path) -> None:
    """With ``slack_percent=0`` the schema cap should equal the prompt cap."""
    payload = {**_MINIMAL_CFG, "slack_percent": 0}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    cfg = load_analysts_config(path=cfg_file)
    assert cfg.schema_cap(200) == 200
    assert cfg.schema_cap(1000) == 1000


def test_load_analysts_config_rejects_slack_percent_out_of_range(tmp_path: Path) -> None:
    """``slack_percent`` is bounded ``[0, 50]`` — a 99% override is operator
    error, not a tuning knob."""
    payload = {**_MINIMAL_CFG, "slack_percent": 99}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_exposes_news_llm_caps(tmp_path) -> None:
    """The loaded config exposes `news.llm.{timeout_seconds, max_output_tokens, timeout_retries, schema_retries}`."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_stale_headlines_per_ticker": 10,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "temperature":       0.3,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_filing_8k_body_chars":   1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "thinking_budget":   2048,
                "temperature":       0.3,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    cfg = load_analysts_config(path=cfg_path)

    assert cfg.news.llm.timeout_seconds   == 60
    assert cfg.news.llm.max_output_tokens == 2000
    assert cfg.news.llm.timeout_retries   == 3
    assert cfg.news.llm.schema_retries    == 3
    # thinking_budget is optional — omitted on the News block, so it defaults
    # to None (leave the model's native thinking behaviour untouched).
    assert cfg.news.llm.thinking_budget is None

    assert cfg.fundamental.llm.timeout_seconds   == 60
    assert cfg.fundamental.llm.max_output_tokens == 2000
    assert cfg.fundamental.llm.timeout_retries   == 3
    assert cfg.fundamental.llm.schema_retries    == 3
    # Set on the Fundamental block — parsed through as the bounded ceiling.
    assert cfg.fundamental.llm.thinking_budget == 2048


def test_llm_caps_accepts_thinking_level(tmp_path: Path) -> None:
    """``thinking_level`` (the Gemini 3 knob) is parsed and exposed."""

    cfg_dict = json.loads(json.dumps(_MINIMAL_CFG))
    cfg_dict["news"]["llm"] = {**_MINIMAL_LLM_CAPS, "thinking_level": "medium"}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(cfg_dict))

    cfg = load_analysts_config(path=cfg_file)

    assert cfg.news.llm.thinking_level == "medium"
    # The two knobs are mutually exclusive, so a level-only block leaves the
    # budget unset.
    assert cfg.news.llm.thinking_budget is None


def test_llm_caps_rejects_both_thinking_knobs(tmp_path: Path) -> None:
    """Setting both thinking knobs is the Gemini 3 400 — reject at config load."""

    cfg_dict = json.loads(json.dumps(_MINIMAL_CFG))
    cfg_dict["news"]["llm"] = {
        **_MINIMAL_LLM_CAPS,
        "thinking_budget": 512,
        "thinking_level":  "medium",
    }
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(cfg_dict))

    with pytest.raises(ValidationError, match="mutually exclusive"):
        load_analysts_config(path=cfg_file)


def test_llm_caps_rejects_invalid_thinking_level(tmp_path: Path) -> None:
    """An unrecognised level fails validation rather than silently passing."""

    cfg_dict = json.loads(json.dumps(_MINIMAL_CFG))
    cfg_dict["news"]["llm"] = {**_MINIMAL_LLM_CAPS, "thinking_level": "ultra"}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(cfg_dict))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_rejects_zero_timeout_seconds(tmp_path) -> None:
    """`timeout_seconds <= 0` raises at load time, not at first use."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   0,                       # invalid
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_filing_8k_body_chars":   1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_path)


def test_load_analysts_config_rejects_tiny_max_output_tokens(tmp_path) -> None:
    """`max_output_tokens < 256` raises at load time."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 100,                     # below ge=256 floor
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_filing_8k_body_chars":   1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_path)


# ---------------------------------------------------------------------------
# Phase 14 Plan 1 — filing-delta settings
# ---------------------------------------------------------------------------

def test_fundamental_litigation_cap_loaded() -> None:
    """``max_filing_litigation_chars`` must load from config/analysts.json.

    The litigation section (Legal Proceedings) joins MD&A and risk factors as
    a diffed prose block in Phase 14; its render cap must be config-driven,
    never hardcoded in the assembly layer.
    """
    from config.analysts import load_analysts_config

    cfg = load_analysts_config()

    assert cfg.fundamental.max_filing_litigation_chars == 1500


def test_fundamental_filing_delta_horizon_loaded() -> None:
    """``filing_delta_horizon_days`` must load from config/analysts.json.

    This is the calendar-day horizon injected as the fundamental verdict's
    ``horizon_days`` — the Lazy Prices drift operates at 3–6 months, and the
    scoreboard-measured horizon is ~90 calendar days (≈3 months).  Calendar
    days so the rendered horizon composes with the strategist clock.
    """
    from config.analysts import load_analysts_config

    cfg = load_analysts_config()

    assert cfg.fundamental.filing_delta_horizon_days == 90


def test_fundamental_risk_cap_raised_for_diffed_survivors() -> None:
    """Risk-factor cap is 4000 now that unchanged paragraphs are stripped first.

    Pre-Phase 14 the cap bounded the raw (mostly boilerplate) section at 1500;
    post-diff it bounds only the year-over-year survivors, so it is raised —
    the same reasoning as the Phase 13 MD&A raise (1500 → 12000).
    """
    from config.analysts import load_analysts_config

    cfg = load_analysts_config()

    assert cfg.fundamental.max_filing_risk_chars == 4000


def test_staleness_similarity_threshold_loads_from_config():
    """The committed config file must carry the Phase 14 staleness threshold."""
    cfg = load_analysts_config()

    assert 0.0 <= cfg.staleness_similarity_threshold <= 1.0
    assert cfg.staleness_similarity_threshold == 0.85


def test_fundamental_filing_similarity_settings_load() -> None:
    """The Phase 14 1b filing-similarity settings must load and validate.

    A missing field must RAISE (loud) rather than silently defaulting — the
    silent-degradation failure mode this project treats as its recurring bug
    class.  We assert the concrete configured values, not just presence.
    """
    caps = get_analysts_config().fundamental

    assert 0.0 <= caps.filing_dedup_cosine <= 1.0
    assert caps.filing_numeric_delta_pct > 0.0
    assert 1 <= caps.filing_history_years <= 15
    assert 0.0 <= caps.filing_scale_low_pct < caps.filing_scale_high_pct <= 1.0
    assert caps.filing_scale_min_history >= 1


def test_news_drift_horizon_days_default():
    """News exposes a config-driven drift horizon (~20 calendar days).

    Set to the scoreboard-measured post-news drift horizon, in calendar days
    so the strategist's rendered horizon composes with its clock.
    """
    from config.analysts import get_analysts_config

    assert get_analysts_config().news.drift_horizon_days == 20


# ---------------------------------------------------------------------------
# Task 6 — fundamental lean recalibration config (trigger/cap/decay/8-K anchor)
# ---------------------------------------------------------------------------

def test_fundamental_recalibration_config():
    """The filing-delta trigger/cap/decay knobs and the 8-K anchor list must
    load from ``config/analysts.json``, ready for Tasks 7/8 to consume.
    """
    from config.analysts import get_analysts_config

    f = get_analysts_config().fundamental

    assert 0.0 <= f.filing_delta_trigger_similarity <= 1.0
    assert 0.0 < f.filing_delta_magnitude_cap <= 1.0
