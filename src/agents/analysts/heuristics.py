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
    """Thresholds for the deterministic technical verdict (three independent reads).

    The verdict's lean/magnitude/confidence reflect ONLY the short-term reversal
    read.  The volatility-regime and trend-state reads are surfaced as rendered
    feature numbers with explanatory prose (see ``contract.strategist_prompt``);
    they are deliberately NOT blended into the lean.
    """

    # ── Read 1: short-term reversal (the one directional lean) ──────────────
    reversal_neutral_band_pct: float = Field(ge=0.0, le=1.0)
    """Neutral band for the contrarian 5-day reversal lean.

    ``pct_change_5d`` is a fractional return (0.03 = +3 %).  When
    ``abs(pct_change_5d)`` is strictly below this band the analyst abstains
    (``lean="neutral"``): the recent move is too small to be a mean-reversion
    candidate.  At or beyond the band edge (the edge is inclusive —
    ``abs(pct5) == band`` already yields a directional lean at
    ``reversal_confidence_base``) the lean is CONTRARIAN: it leans AGAINST the
    recent move (a recent up-move → bearish fade; a recent down-move → bullish
    bounce), inverting the old trend-following sign (Jegadeesh 1990; Lehmann
    1990).  Tune via ``config/analyst_heuristics.json`` without a code change.
    """

    reversal_magnitude_scale: float = Field(gt=0.0)
    """Scales the size of the faded move into a magnitude.

    ``magnitude = min(abs(pct_change_5d) * this, magnitude_cap)``.  With the
    default 8.0 a 5 % 5-day move yields magnitude 0.40 and a 12.5 % move
    saturates the cap.
    """

    reversal_confidence_base: float = Field(ge=0.0, le=1.0)
    """Confidence at the neutral-band edge for a directional reversal lean.

    Confidence ramps linearly from this base (at the band edge) to 1.0 (at
    twice the band width), so a borderline fade is low-confidence and a large
    dislocation is high-confidence.  A neutral lean carries confidence 0.0 (no
    directional view to be confident about).
    """

    reversal_horizon_days: int = Field(ge=1, le=60)
    """Trading-day horizon the reversal read targets (5–10 days).

    Written onto ``AnalystVerdict.horizon_days`` by ``derive_technical_verdict``
    so the strategist can read the technical analyst's own horizon.  Kept short
    and deliberately away from the sub-24h overnight bounce.
    """

    # ── Read 2: volatility regime (risk number; NOT blended into the lean) ──
    vol_regime_window: int = Field(ge=2, le=252)
    """Trailing window (in valid ATR% samples) for the volatility-regime z-score.

    ``vol_regime_z = (atr_pct[-1] - mean(atr_pct[-window:])) / std(...)`` — a
    self-relative read of how stressed volatility is versus the ticker's own
    recent history (Moreira–Muir 2017; safe daily via volatility clustering,
    Engle 1982 / Bollerslev 1986).
    """

    vol_regime_extreme_z: float = Field(gt=0.0)
    """``abs(vol_regime_z)`` at or above which the ``vol_regime_extreme`` tag fires.

    The threshold is applied to the *absolute* z, so the tag fires at either
    tail — a stressed (high positive z) regime OR an unusually calm (large
    negative z) one — hence "extreme" rather than "elevated".  A risk flag
    only — it does NOT alter the reversal lean.
    """

    # ── Retained context knobs (drive corroborating TAGS only) ─────────────
    vol_ratio_breakout: float = Field(gt=1.0)
    vol_ratio_dry_up: float   = Field(gt=0.0, lt=1.0)
    near_52w_extreme_pct: float = Field(gt=0.0)
    magnitude_cap: float        = Field(gt=0.0, le=1.0)

    beta_confidence_damping_enabled: bool = Field(default=False)
    """Gate the ``beta_confidence_damping`` technical feature (strategist-facing
    context only; ``derive_technical_verdict`` never reads it).  Ships disabled.
    """


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
