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
    momentum_neutral_band_pct: float = Field(ge=0.0, le=1.0)
    """Conviction gate for the base lean.

    If ``abs(pct_change_20d)`` is strictly less than this value, the analyst
    abstains and emits ``lean="neutral"`` regardless of the sign of the
    20-day return.  This prevents the analyst from expressing a directional
    view on negligible momentum.

    Units: fractional return — the same units as ``pct_change_20d`` (e.g.
    ``0.02`` means ±2 %).  The extractor computes ``pct_change_20d`` as
    ``(close[-1] / close[-21]) - 1.0``.

    Value is provisional — pending a measured sweep against the eval
    scoreboard.  Adjust via ``config/analyst_heuristics.json`` without any
    code change.
    """

    rsi_mean_reversion: float = Field(default=35.0, ge=0.0, le=50.0)
    """Moderate-oversold mean-reversion guard for the 20-day bearish lean.

    When a name is called bearish (sign of 20d return is negative) and its
    RSI is strictly below this threshold, the call is downgraded to neutral
    rather than propagated as bearish.  Back-testing shows moderate-oversold
    names (RSI 25–35) tend to mean-revert at the 20-day horizon, making a
    straight bearish call anti-predictive in that band.

    The stronger RSI<``rsi_oversold`` capitulation flip (→ bullish when
    pct_change_5d < 0) still wins for genuinely capitulating names because
    25 < 35 — an RSI-20 name is first neutralised by this rule and then
    re-promoted to bullish by the capitulation branch.

    Set to ``0.0`` to disable this rule entirely (the condition
    ``rsi < 0.0`` can never be true for a real RSI value).

    Valid range: 0–50 (must not exceed ``rsi_oversold``'s upper bound).
    Tune via ``config/analyst_heuristics.json`` without a code change.
    """

    suppress_bearish_under_golden_cross: bool = Field(default=True)
    """Regime gate: neutralise a bearish 20-day lean while a golden cross holds.

    The Phase-13 technical audit (docs/Phase13-analyst-improvement/audit/
    technical-audit.md §6/§7) found the bearish lean to be anti-predictive in
    this large-cap universe (sub-50 % hit rate at every horizon), and *most*
    so when the name is simultaneously in a confirmed up-trend regime:
    ``bearish + golden_cross`` (n=24) posted a 21 % down-rate and +2.23 %
    mean +20-day return — i.e. the analyst was loudly bearish on names that
    kept rising.  A modest negative 20-day blip inside a multi-month uptrend
    is noise, not a reversal.

    When ``True`` (default), a bearish base lean is downgraded to neutral
    whenever ``golden_cross`` is set, and the ``bearish_suppressed_golden_cross``
    factor is appended for traceability.  Set to ``False`` to restore the
    regime-blind behaviour.

    Tune via ``config/analyst_heuristics.json`` without a code change.
    """

    suppress_bullish_under_death_cross: bool = Field(default=True)
    """Regime gate: neutralise a bullish 20-day lean while a death cross holds.

    The symmetric counterpart to ``suppress_bearish_under_golden_cross``.  The
    offline replay over the four audited runs (644 decisions) found
    ``bullish + death_cross`` (n=24) was a genuinely poor call — a 46 % up-rate
    and a **−0.71 %** mean +20-day return — i.e. a short-term positive blip
    against a confirmed down-trend regime tended to fade.  Suppressing it lifted
    the overall bullish +20-day hit rate from 58.4 % to 59.5 % and bullish mean
    +20-day return from +1.95 % to +2.18 %, with no offsetting downside.

    When ``True`` (default), a bullish base lean is downgraded to neutral
    whenever ``death_cross`` is set, and the ``bullish_suppressed_death_cross``
    factor is appended for traceability.  Set to ``False`` to restore the
    regime-blind behaviour.

    Tune via ``config/analyst_heuristics.json`` without a code change.
    """

    directional_52w_confidence: bool = Field(default=True)
    """Make the 52-week-proximity confidence boost corroborate the lean.

    The audit found the unconditional ``near_52w_low`` boost actively harmful:
    bearish names near their 52-week low had a 35 % down-rate (vs 51 % without
    the tag) and +2.09 % mean +20-day return — the boost lifted confidence to
    0.90 precisely on the falling names most likely to bounce.  Proximity to
    the low is a *mean-reversion* zone, not a continuation signal.

    When ``True`` (default), the proximity boost only fires when it corroborates
    the lean:

    - ``near_52w_high`` boosts confidence only on a **bullish** lean.
    - ``near_52w_low``  boosts confidence only on a **bullish** lean (a name
      bouncing off its low) — never on a bearish lean.

    The context *factors* (``near_52w_high`` / ``near_52w_low``) are still
    emitted regardless of lean — only the confidence arithmetic is gated.

    Set to ``False`` to restore the legacy unconditional ``+confidence_boost_step``
    on either proximity.  Tune via ``config/analyst_heuristics.json``.
    """

    momentum_band_confidence_floor: float = Field(default=0.5, ge=0.0, le=1.0)
    """Confidence damping for directional calls just outside the neutral band.

    The analyst is stateless per tick, so literal hysteresis on the lean is not
    available.  Instead we damp *confidence* as ``abs(pct_change_20d)`` shrinks
    toward ``momentum_neutral_band_pct``: a call that only just cleared the band
    (a borderline whipsaw) should be low-confidence, while a call well beyond
    the band keeps full confidence.

    A linear ramp scales the post-modifier confidence by a factor that runs
    from this floor (at the band edge) up to ``1.0`` (at twice the band):

        ramp = floor + (1 - floor) * clamp((|pct20| - band) / band, 0, 1)

    With the defaults (band 0.02, floor 0.5) a name at exactly +2 % 20-day keeps
    half its confidence; a name at +4 % or beyond keeps all of it.  Neutral
    leans (inside the band) are unaffected — they carry no directional
    confidence to damp.

    Set to ``1.0`` to disable the damping entirely (ramp collapses to a constant
    1.0).  Tune via ``config/analyst_heuristics.json`` without a code change.
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
