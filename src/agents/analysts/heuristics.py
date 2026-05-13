"""Typed loader for `config/analyst_heuristics.json`.

Models every section of the heuristics file as a frozen Pydantic class so
out-of-range or unknown values fail at boot rather than at tick 1. The
`load_heuristics()` accessor is cached via `lru_cache(maxsize=1)` — same
pattern as `src/data/config.py::get_config()`. Hot-reload is intentionally
not supported (see spec §Configuration).
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# Default path relative to repo root. Overridable via env var for tests.
_DEFAULT_PATH = Path("config/analyst_heuristics.json")


class _Frozen(BaseModel):
    """Common config base — frozen, no unknown keys, no defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class TechnicalHeuristics(_Frozen):
    """Thresholds for the deterministic technical verdict."""

    rsi_overbought: float            = Field(ge=50.0, le=100.0)
    rsi_oversold: float              = Field(ge=0.0, le=50.0)
    pct_change_momentum_scale: float = Field(gt=0.0)
    vol_ratio_breakout: float        = Field(gt=1.0)
    vol_ratio_dry_up: float          = Field(gt=0.0, lt=1.0)
    atr_high_volatility_pct: float   = Field(gt=0.0)
    near_52w_extreme_pct: float      = Field(gt=0.0)
    confidence_base: float           = Field(ge=0.0, le=1.0)
    confidence_boost_step: float     = Field(ge=0.0, le=1.0)
    confidence_penalty_step: float   = Field(ge=0.0, le=1.0)
    magnitude_cap: float             = Field(gt=0.0, le=1.0)


class SocialHeuristics(_Frozen):
    """Thresholds for the deterministic social verdict."""

    score_neutral_band: float               = Field(ge=0.0, le=1.0)
    score_to_magnitude_scale: float         = Field(gt=0.0)
    high_volume_mentions: int               = Field(gt=0)
    high_volume_magnitude_boost: float      = Field(ge=0.0, le=1.0)
    confidence_volume_floor: int            = Field(ge=0)
    platform_disagreement_threshold: float  = Field(ge=0.0, le=1.0)
    confidence_base: float                  = Field(ge=0.0, le=1.0)
    confidence_boost_step: float            = Field(ge=0.0, le=1.0)
    confidence_penalty_step: float          = Field(ge=0.0, le=1.0)
    magnitude_cap: float                    = Field(gt=0.0, le=1.0)


class SmartMoneyHeuristics(_Frozen):
    """Thresholds for the deterministic smart-money verdict."""

    multi_filer_min_count: int          = Field(ge=1)
    high_activity_trade_count: int      = Field(ge=1)
    lone_filer_confidence_floor: float  = Field(ge=0.0, le=1.0)
    consensus_confidence_ceiling: float = Field(ge=0.0, le=1.0)
    magnitude_cap: float                = Field(gt=0.0, le=1.0)


class FundamentalVocabulary(_Frozen):
    """Closed-vocabulary tag lists for the narrowed Fundamental LLM."""

    guidance: list[str]        = Field(min_length=1)
    tone: list[str]            = Field(min_length=1)
    risks: list[str]           = Field(min_length=1)
    insider_signals: list[str] = Field(min_length=1)


class NewsVocabulary(_Frozen):
    """Closed-vocabulary tag lists for the narrowed News LLM."""

    catalysts: list[str] = Field(min_length=1)
    novelty: list[str]   = Field(min_length=1)
    direction: list[str] = Field(min_length=1)


class GoldenSetConfig(_Frozen):
    """Tunables for the golden-set sanity test."""

    min_direction_agreement_pct: int = Field(ge=0, le=100)


class AnalystHeuristics(_Frozen):
    """Top-level config object — one per JSON file."""

    technical: TechnicalHeuristics
    social: SocialHeuristics
    smart_money: SmartMoneyHeuristics
    fundamental_vocabulary: FundamentalVocabulary
    news_vocabulary: NewsVocabulary
    golden_set: GoldenSetConfig


@lru_cache(maxsize=1)
def load_heuristics() -> AnalystHeuristics:
    """Read `config/analyst_heuristics.json` (or `ANALYST_HEURISTICS_PATH`) and validate.

    The path is resolved from the current working directory (project root) by
    default. Override via the ``ANALYST_HEURISTICS_PATH`` environment variable
    — useful in tests to point at a temporary file without touching the source tree.

    Raises:
        FileNotFoundError: if the JSON file does not exist at the resolved path.
        json.JSONDecodeError: if the file content is not valid JSON.
        pydantic.ValidationError: if the parsed payload fails schema validation.

    Returns:
        A validated, immutable ``AnalystHeuristics`` instance.
    """
    path = Path(os.environ.get("ANALYST_HEURISTICS_PATH", str(_DEFAULT_PATH)))
    raw = json.loads(path.read_text())
    return AnalystHeuristics.model_validate(raw)
