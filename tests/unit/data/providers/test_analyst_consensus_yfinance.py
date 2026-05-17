"""Unit tests for ``data.providers.analyst_consensus.yfinance``.

All yfinance calls are monkeypatched — no real network traffic occurs.

Key invariants tested
---------------------
- **Happy path**: ``fetch`` returns ``(AnalystRating, list[AnalystRevision])``
  with ``target_mean`` and ``recommendation_mean`` populated, and at least one
  revision mapped to a controlled action literal.
- **Action mapping**: known action strings (``"up"``, ``"down"``, ``"init"``,
  ``"main"``) resolve to the correct ``AnalystRevision.action`` literal;
  unrecognised strings map to ``"unknown"``.
- **Snapshot-only warning**: a ``UserWarning`` is emitted when ``as_of`` is
  more than 7 days in the past.
- **Sparse data graceful handling**: when ``analyst_price_targets`` / ``info``
  / ``upgrades_downgrades`` return empty/None, the provider returns sensible
  defaults without raising.
- **max_revisions cap**: only the ``max_revisions`` newest entries are returned.
"""
from __future__ import annotations

import warnings
from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_ticker(
    *,
    analyst_price_targets: dict | None = None,
    upgrades_downgrades: pd.DataFrame | None = None,
    recommendations_summary: pd.DataFrame | None = None,
    info: dict | None = None,
) -> MagicMock:
    """Build a minimal ``yfinance.Ticker`` mock.

    Parameters
    ----------
    analyst_price_targets:
        Dict returned by ``Ticker.analyst_price_targets``.
    upgrades_downgrades:
        DataFrame returned by ``Ticker.upgrades_downgrades``.
    recommendations_summary:
        DataFrame returned by ``Ticker.recommendations_summary``.
    info:
        Dict returned by ``Ticker.info``.

    Returns
    -------
    MagicMock
        Configured mock whose attributes mirror the real ``yfinance.Ticker``
        attributes used by the provider.
    """
    mock = MagicMock()
    mock.analyst_price_targets = analyst_price_targets or {}
    mock.upgrades_downgrades   = upgrades_downgrades
    mock.recommendations_summary = recommendations_summary
    mock.info = info or {}
    return mock


def _make_ud_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal upgrades_downgrades DataFrame from a list of row dicts.

    Each dict must contain keys: ``GradeDate``, ``Firm``, ``Action``,
    ``FromGrade``, ``ToGrade``.

    Parameters
    ----------
    rows:
        List of dicts, one per revision event.

    Returns
    -------
    pd.DataFrame
    """
    df = pd.DataFrame(rows)
    # Set GradeDate as the index to mirror the real yfinance structure (the
    # provider calls reset_index() to turn it back into a column).
    if "GradeDate" in df.columns:
        df = df.set_index("GradeDate")
    return df


def _make_rec_summary(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal recommendations_summary DataFrame.

    Parameters
    ----------
    rows:
        List of dicts with keys: ``period``, ``strongBuy``, ``buy``,
        ``hold``, ``sell``, ``strongSell``.

    Returns
    -------
    pd.DataFrame
    """
    return pd.DataFrame(rows)


# ── Happy-path test ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_returns_rating_and_revisions(monkeypatch):
    """Full happy-path: provider returns populated ``AnalystRating`` and a
    non-empty ``list[AnalystRevision]`` when yfinance returns well-formed data.

    Asserts
    -------
    - ``rating.ticker`` == ``"AAPL"``
    - ``rating.target_mean`` is populated from ``analyst_price_targets["mean"]``
    - ``rating.recommendation_mean`` is computed from ``recommendations_summary``
    - ``rating.number_of_analysts`` is populated from ``info``
    - At least one revision is returned with a valid ``action`` literal
    - Revisions are ordered newest-first
    """
    from data.providers.analyst_consensus import yfinance as mod

    ud_df = _make_ud_df([
        {
            "GradeDate": date(2023, 3, 10),
            "Firm":      "Goldman Sachs",
            "Action":    "up",
            "FromGrade": "Neutral",
            "ToGrade":   "Buy",
        },
        {
            "GradeDate": date(2023, 3, 5),
            "Firm":      "Morgan Stanley",
            "Action":    "main",
            "FromGrade": "Overweight",
            "ToGrade":   "Overweight",
        },
    ])

    rec_df = _make_rec_summary([
        {
            "period":    "0m",
            "strongBuy": 10,
            "buy":       5,
            "hold":      3,
            "sell":      1,
            "strongSell": 0,
        }
    ])

    mock_ticker = _make_ticker(
        analyst_price_targets={"current": 175.0, "high": 200.0, "low": 150.0, "mean": 178.0, "median": 177.0},
        upgrades_downgrades=ud_df,
        recommendations_summary=rec_df,
        info={"numberOfAnalystOpinions": 19},
    )

    monkeypatch.setattr(mod.yf, "Ticker", lambda _sym: mock_ticker)

    # Use a recent as_of to avoid the snapshot-only UserWarning.
    as_of = date.today() - timedelta(days=3)
    rating, revisions = await mod.fetch("aapl", as_of=as_of)

    # ── AnalystRating assertions ───────────────────────────────────────────────
    assert rating.ticker == "AAPL"
    assert rating.as_of == as_of
    assert rating.target_mean == 178.0
    assert rating.target_high == 200.0
    assert rating.target_low  == 150.0
    assert rating.target_median == 177.0
    assert rating.number_of_analysts == 19
    # Weighted mean: (1×10 + 2×5 + 3×3 + 4×1 + 5×0) / 19 = (10+10+9+4) / 19 = 33/19
    assert rating.recommendation_mean is not None
    assert abs(rating.recommendation_mean - 33 / 19) < 1e-6

    # ── AnalystRevision assertions ─────────────────────────────────────────────
    assert len(revisions) == 2
    # Newest-first: 2023-03-10 before 2023-03-05
    assert revisions[0].event_date == date(2023, 3, 10)
    assert revisions[0].firm   == "Goldman Sachs"
    assert revisions[0].action == "upgrade"
    assert revisions[0].from_grade == "Neutral"
    assert revisions[0].to_grade   == "Buy"

    assert revisions[1].event_date == date(2023, 3, 5)
    assert revisions[1].action == "reiterate"


# ── Action mapping tests ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("up",        "upgrade"),
    ("Upgrade",   "upgrade"),
    ("down",      "downgrade"),
    ("Down",      "downgrade"),
    ("init",      "initiate"),
    ("Initiates", "initiate"),
    ("main",      "reiterate"),
    ("Maintains", "reiterate"),
    ("raised",    "target_raise"),
    ("lowered",   "target_cut"),
    ("cut",       "target_cut"),
    ("BOGUS",     "unknown"),
    (None,        "unknown"),
    ("",          "unknown"),
])
def test_normalise_action(raw, expected):
    """``_normalise_action`` maps known strings and falls back to ``"unknown"``.

    Parameters
    ----------
    raw:
        Raw input string (or None).
    expected:
        Expected ``AnalystRevision.action`` literal.
    """
    from data.providers.analyst_consensus.yfinance import _normalise_action

    assert _normalise_action(raw) == expected


# ── Snapshot-only warning test ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_warns_when_as_of_is_stale(monkeypatch):
    """A ``UserWarning`` must be emitted when ``as_of < today − 7 days``.

    The provider still returns data — the warning is informational.
    """
    from data.providers.analyst_consensus import yfinance as mod

    mock_ticker = _make_ticker(
        analyst_price_targets={},
        upgrades_downgrades=pd.DataFrame(),
        recommendations_summary=pd.DataFrame(),
        info={},
    )
    monkeypatch.setattr(mod.yf, "Ticker", lambda _sym: mock_ticker)

    stale_as_of = date.today() - timedelta(days=30)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rating, revisions = await mod.fetch("AAPL", as_of=stale_as_of)

    user_warnings = [w for w in caught if issubclass(w.category, UserWarning)]
    assert len(user_warnings) >= 1
    assert "more than 7 days in the past" in str(user_warnings[0].message)

    # Data is still returned despite the warning.
    assert rating.ticker == "AAPL"
    assert revisions == []


# ── Sparse data graceful-handling test ────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_handles_empty_yfinance_data(monkeypatch):
    """Provider must not raise when all yfinance attributes return empty/None.

    Asserts
    -------
    - Returns ``(AnalystRating, [])`` without raising.
    - All optional fields on ``AnalystRating`` are ``None``.
    """
    from data.providers.analyst_consensus import yfinance as mod

    mock_ticker = _make_ticker(
        analyst_price_targets={},
        upgrades_downgrades=None,
        recommendations_summary=None,
        info={},
    )
    monkeypatch.setattr(mod.yf, "Ticker", lambda _sym: mock_ticker)

    as_of = date.today() - timedelta(days=1)
    rating, revisions = await mod.fetch("TSLA", as_of=as_of)

    assert rating.ticker == "TSLA"
    assert rating.target_mean         is None
    assert rating.target_high         is None
    assert rating.target_low          is None
    assert rating.target_median       is None
    assert rating.recommendation_mean is None
    assert rating.number_of_analysts  is None
    assert revisions == []


# ── max_revisions cap test ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fetch_caps_revisions_at_max_revisions(monkeypatch):
    """Only the ``max_revisions`` newest revisions are returned.

    Build a DataFrame with 5 revision rows and request ``max_revisions=2``.
    """
    from data.providers.analyst_consensus import yfinance as mod

    rows = [
        {"GradeDate": date(2023, 3, d), "Firm": f"Firm{d}", "Action": "main",
         "FromGrade": "Buy", "ToGrade": "Buy"}
        for d in range(1, 6)  # 5 rows: 2023-03-01 through 2023-03-05
    ]
    ud_df = _make_ud_df(rows)

    mock_ticker = _make_ticker(
        analyst_price_targets={},
        upgrades_downgrades=ud_df,
        recommendations_summary=pd.DataFrame(),
        info={},
    )
    monkeypatch.setattr(mod.yf, "Ticker", lambda _sym: mock_ticker)

    as_of = date.today() - timedelta(days=1)
    _, revisions = await mod.fetch("MSFT", as_of=as_of, max_revisions=2)

    assert len(revisions) == 2
    # Newest-first: 2023-03-05, then 2023-03-04
    assert revisions[0].event_date == date(2023, 3, 5)
    assert revisions[1].event_date == date(2023, 3, 4)


# ── Integration smoke (slow / network) ───────────────────────────────────────

@pytest.mark.slow
@pytest.mark.asyncio
async def test_analyst_consensus_integration_real_network():
    """Live call to yfinance for AAPL analyst consensus.

    Marked ``@pytest.mark.slow`` — excluded from the default test run.
    Does not pin exact numbers (the yfinance dataset updates daily).

    Asserts
    -------
    - Returns ``(AnalystRating, list[AnalystRevision])`` without raising.
    - ``rating.ticker`` == ``"AAPL"``.
    - If revisions are returned, each has a valid ``action`` literal.
    """
    from typing import get_args

    from data.models.analyst_consensus import AnalystRevision
    from data.providers.analyst_consensus import yfinance as mod

    # Extract the valid action literals from the Pydantic model's type annotation.
    # ``model_fields`` gives us the resolved ``FieldInfo``; ``get_args`` on its
    # ``annotation`` returns the tuple of Literal values reliably across Python
    # versions and Pydantic v2.
    action_annotation = AnalystRevision.model_fields["action"].annotation
    valid_actions = set(get_args(action_annotation))
    as_of = date.today()

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        rating, revisions = await mod.fetch("AAPL", as_of=as_of)

    assert rating.ticker == "AAPL"
    assert rating.as_of == as_of

    for rev in revisions:
        assert rev.action in valid_actions, (
            f"Unexpected action {rev.action!r} for {rev.firm}"
        )
