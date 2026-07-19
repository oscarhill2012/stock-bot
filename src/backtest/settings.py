"""Typed loader for ``config/backtest_settings.json``.

Mirrors the shape of ``src/data/config.py``:  a Pydantic model, an
``lru_cache``-style singleton, and a test-only ``_reset_cache`` hook.  Five
scripts and the backtest Runner currently parse the JSON with raw
``json.loads`` calls; this loader replaces every one of them so the schema
is validated once and consumed uniformly.

``extra="forbid"`` is deliberate.  The Phase 7.5 schedule rewrite deleted
``tz`` / ``open_time`` / ``close_time``; a stale config file with those
keys must fail loudly rather than be silently ignored — otherwise the
intent of the deletion is lost.

Per-window storage layout
-------------------------
Cache and run artefacts live under ``<backtests_root>/<window-name>/``:

    backtests/
    └── svb-stress-2023-03/
        ├── store.sqlite          # golden cache for this window only
        └── runs/
            └── svb-stress-2023-03-abc1234/
                ├── manifest.json
                ├── db.sqlite
                └── ...

The previous layout — one shared cache + one shared runs tree — mixed
window data into a single SQLite file which made it hard to inspect or
delete a single window's data without bespoke SQL.  ``cache_path_for_window``
and ``runs_root_for_window`` are the only sanctioned ways to derive a path
from the settings; helpers ensure callers cannot accidentally collide windows
by hand-rolling the join.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class BacktestSettings(BaseModel):
    """Validated contents of ``config/backtest_settings.json``.

    Schedule timing keys (``tz`` / ``open_time`` / ``close_time``) are
    deliberately absent — ``pandas_market_calendars`` owns NYSE session
    times.  Only ``ticks_per_day`` is a real policy knob.
    ``ohlcv_warmup_days`` has a default because the SVB-2023 backfill
    landed it as a tactical add-on; legacy files without the field still
    load.  The binding constraint is the technical feature ``vol_regime_z``
    (a z-score of ATR% over the ``vol_regime_window`` rolling window, 60
    bars per ``config/analyst_heuristics.json``): it needs 60 + 14 = 74
    trading bars before it can emit a value, since the underlying ATR(14)
    series itself has 14 bars of NaN warmup before the 60-bar z-window can
    even start filling.  120 calendar days ≈ 82 trading bars, clearing that
    74-bar floor with margin, while still comfortably covering the
    shallower 50-bar features, RSI(14), and ATR(14) on their own.

    ``backtests_root`` is the single root directory under which every
    window's cache and runs are nested.  Per-window paths are computed by
    ``cache_path_for_window`` / ``runs_root_for_window``.
    """

    model_config = ConfigDict(extra="forbid")

    backtests_root:                str
    ticks_per_day:                 list[str]
    failed_tick_abort_ratio:       float = Field(ge=0.0, le=1.0)
    fake_broker_starting_cash:     float
    forward_return_horizons_days:  list[int]
    ohlcv_warmup_days:             int = 120

    # ── Scoreboard evaluation settings ────────────────────────────────────────

    # Per-analyst primary scoring horizon (calendar days).  The scoreboard
    # reports ALL horizons but ranks analysts using this per-type horizon.
    # News signals decay quickly (~1 day); fundamental signals persist (~20 days).
    # Analysts NOT present in this map fall back to max(forward_return_horizons_days).
    # Example: {"news": 1, "fundamental": 20, "technical": 5}
    primary_horizon_by_analyst: dict[str, int] = Field(
        default_factory=lambda: {"news": 1, "fundamental": 20},
    )

    # Cross-sectional neutralisation mode for the scoreboard excess metric.
    # "sector"   → subtract the per-tick mean of the ticker's GICS sector peers
    #              (uses CachedDataStore.read_company_ratios to look up sector).
    #              Tickers whose sector is unavailable in the cache fall back to
    #              "universe" mode for that ticker only, with a WARNING logged.
    # "universe" → subtract the per-tick whole-universe cross-sectional mean
    #              (original behaviour; no sector lookup required).
    scoreboard_neutralise_by: str = Field(
        default="sector",
        pattern=r"^(sector|universe)$",
    )

    # Inference mode for the scoreboard t-stat / p-value.
    #
    # The scored observations are NOT independent: for a given ticker the
    # verdict persists across ticks and the overlapping forward-return windows
    # (+5d / +20d) induce strong serial autocorrelation, plus some
    # cross-sectional correlation within a tick.  A naive one-sample t-test
    # (``scipy.stats.ttest_1samp``) treats every observation as independent and
    # therefore UNDERSTATES the standard error — inflating |t| and shrinking p,
    # so genuinely-noisy analysts read as "significant".
    #
    # "cluster_ticker" → cluster-robust (sandwich) standard error clustered by
    #                    ticker.  Captures the dominant within-ticker temporal
    #                    autocorrelation while remaining deterministic and cheap.
    #                    Degrees of freedom = (#clusters − 1).  On genuinely
    #                    i.i.d. data (every ticker a singleton cluster) it
    #                    reduces to the naive estimator, so it never spuriously
    #                    moves a result that had no autocorrelation to correct.
    # "naive"          → the original ``ttest_1samp`` (over-confident; retained
    #                    only as an explicit, opt-in escape hatch and for A/B
    #                    comparison in the audit).
    scoreboard_inference: str = Field(
        default="cluster_ticker",
        pattern=r"^(cluster_ticker|naive)$",
    )

    # Number of confidence buckets for the scoreboard's confidence-gradient
    # view (Phase 14, iter-11).  Confidence is a continuous float in [0, 1]
    # (not categorical), and its real-world range varies sharply per analyst
    # (e.g. technical spans ~0.14-0.9, fundamental ~0.6-0.85), so a single
    # global numeric cutoff would be meaningless across analysts.  Buckets are
    # therefore DATA-DRIVEN: computed per (analyst, horizon) as quantile cuts
    # over that analyst's own directional (non-neutral) confidence values at
    # its primary horizon, rather than a hardcoded threshold.  This setting
    # only controls how many quantile buckets to cut into (3 = terciles).
    scoreboard_confidence_buckets: int = Field(default=3, ge=2)


_DEFAULT_PATH:                 Path = Path("config/backtest_settings.json")
_cache: BacktestSettings | None      = None


def load_backtest_settings_from(path: Path) -> BacktestSettings:
    """Load and validate the settings file from a specific path.

    Used by tests that need to point the loader at a temporary file.

    Parameters
    ----------
    path:
        Filesystem path to a JSON file matching the ``BacktestSettings``
        schema.

    Returns
    -------
    BacktestSettings
        The validated settings.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    return BacktestSettings.model_validate(payload)


def get_backtest_settings() -> BacktestSettings:
    """Return the cached ``BacktestSettings`` singleton.

    Loads from ``config/backtest_settings.json`` on first call; subsequent
    calls return the cached instance.
    """
    global _cache
    if _cache is None:
        _cache = load_backtest_settings_from(_DEFAULT_PATH)
    return _cache


def _reset_cache() -> None:
    """Test-only hook to drop the singleton so the next call reloads.

    Matches the ``_reset_cache`` hook in ``src/data/config.py``.
    """
    global _cache
    _cache = None


# ── Per-window path helpers ────────────────────────────────────────────────

def cache_path_for_window(settings: BacktestSettings, window: str) -> Path:
    """Return the golden-cache SQLite path for a specific window.

    Layout: ``<backtests_root>/<window>/store.sqlite``.  Always the same
    filename so tooling does not need to know the window to open it once the
    directory is fixed.

    Parameters
    ----------
    settings:
        Loaded ``BacktestSettings`` instance.
    window:
        Window key from ``config/backtest_windows.json``
        (e.g. ``"svb-stress-2023-03"``).

    Returns
    -------
    Path
        The cache path.  Parent directories may not exist yet — callers
        that write are expected to ``mkdir(parents=True, exist_ok=True)``.
    """
    return Path(settings.backtests_root) / window / "store.sqlite"


def runs_root_for_window(settings: BacktestSettings, window: str) -> Path:
    """Return the runs directory for a specific window.

    Layout: ``<backtests_root>/<window>/runs/``.

    Parameters
    ----------
    settings:
        Loaded ``BacktestSettings`` instance.
    window:
        Window key (e.g. ``"svb-stress-2023-03"``).

    Returns
    -------
    Path
        The runs root.  Individual runs land under
        ``<this>/<run-id>/``.
    """
    return Path(settings.backtests_root) / window / "runs"


# A run-id is ``<window-key>-<7-char git sha>``.  ``_git_sha(length=7)`` in
# ``backtest.runner`` produces 7 lower-case hex chars; we anchor to that to
# avoid eating legitimate trailing chunks of a window slug.
_RUN_ID_RE = re.compile(r"^(?P<window>.+)-(?P<sha>[0-9a-f]{7})$")


def window_from_run_id(run_id: str) -> str:
    """Extract the window key from a run-id of the form ``<window>-<sha7>``.

    Run-IDs follow the format ``<window-key>-<7-char git sha>`` (see
    ``_git_sha(length=7)`` in ``backtest.runner``).  We recover the window
    key by stripping the trailing 7-char hex sha (plus the dash that joins
    them).

    Parameters
    ----------
    run_id:
        Run identifier, e.g. ``"svb-stress-2023-03-abc1234"``.

    Returns
    -------
    str
        The window key, e.g. ``"svb-stress-2023-03"``.

    Raises
    ------
    ValueError
        If ``run_id`` does not match the expected shape.
    """
    m = _RUN_ID_RE.match(run_id)
    if not m:
        raise ValueError(
            f"run_id {run_id!r} does not match <window>-<sha7> format"
        )
    return m.group("window")
