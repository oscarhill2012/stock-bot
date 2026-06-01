"""Unit tests for the ``make_evidence_callback`` joiner's missing-ticker branch.

When the LLM omits a verdict for a ticker that is in the watchlist, the joiner
must synthesise a no-data ``AnalystEvidence`` record so downstream consumers
always receive exactly one record per ticker.

This file pins the required shape of that synthesised record so the refactor
in A-015 (routing through ``_no_data_analyst_verdict``) cannot silently change
the observable contract.
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from agents.analysts._common import make_evidence_callback

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_AS_OF = datetime(2023, 3, 15, 9, 30, tzinfo=UTC)
TICKER = "AAPL"
MISSING_TICKER = "MSFT"  # deliberately absent from the LLM verdicts dict

# Use a valid AnalystName literal so the AnalystEvidence Pydantic model accepts it.
ANALYST: str = "technical"


def _extractor(raw: dict, ticker: str, *, as_of: datetime, state: dict) -> dict[str, float]:
    """Minimal feature extractor stub — returns a single constant feature.

    Parameters
    ----------
    raw:
        Per-ticker raw data slice (ignored in this stub).
    ticker:
        Ticker symbol (ignored in this stub).
    as_of:
        Historical timestamp for time-delta features (ignored in this stub).
    state:
        Full pipeline state snapshot (ignored in this stub).

    Returns
    -------
    dict[str, float]
        Constant stub feature vector.
    """
    return {"stub_feature": 1.0}


def _make_state(*, tickers: list[str], verdicts: list[dict]) -> dict:
    """Build a minimal state dict for the callback under test.

    Parameters
    ----------
    tickers:
        Watchlist symbols to iterate over.
    verdicts:
        List of LLM-emitted verdict dicts (may be missing entries for some tickers).

    Returns
    -------
    dict
        Plain dict mimicking ADK's state for unit-test purposes.
    """
    return {
        "tickers": tickers,
        "tick_id": "tick-001",
        "as_of": FIXED_AS_OF.isoformat(),
        f"temp:{ANALYST}_data": {t: {} for t in tickers},
        f"{ANALYST}_verdicts": verdicts,
    }


# A complete, valid verdict dict for TICKER as emitted by the LLM.
# Defined once here so both tests reference the same literal and cannot
# silently diverge if the shape is ever updated.
PRESENT_VERDICT: dict = {
    "ticker": TICKER,
    "lean": "bullish",
    "magnitude": 0.5,
    "confidence": 0.7,
    "rationale": "strong momentum",
    "key_factors": ["rsi"],
    "is_no_data": False,
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_joiner_missing_ticker_produces_no_data_evidence() -> None:
    """When the LLM omits a ticker's verdict, the joiner synthesises a no-data record.

    The synthesised ``AnalystEvidence`` must have:
    - ``verdict.is_no_data is True``
    - ``verdict.report is None``
    - ``verdict.rationale == "no verdict from LLM"``
    - ``ticker`` correctly attached
    - ``analyst`` correctly attached
    """
    # Build a verdict list that covers TICKER but deliberately omits MISSING_TICKER.
    state = _make_state(
        tickers=[TICKER, MISSING_TICKER],
        verdicts=[PRESENT_VERDICT],
    )
    ctx = SimpleNamespace(state=state)

    callback = make_evidence_callback(
        analyst=ANALYST,
        extractor=_extractor,
        verdicts_state_key=f"{ANALYST}_verdicts",
    )

    callback(ctx)

    # Retrieve the written evidence list keyed by the analyst name.
    evidence_list: list[dict] = state[f"{ANALYST}_evidence"]

    # Locate the evidence record for the MISSING ticker.
    missing_records = [ev for ev in evidence_list if ev["ticker"] == MISSING_TICKER]
    assert len(missing_records) == 1, (
        f"Expected exactly one evidence record for {MISSING_TICKER}; got {len(missing_records)}"
    )

    ev = missing_records[0]
    verdict = ev["verdict"]

    # Pin the three canonical no-data shape fields (A-015 contract).
    assert verdict["is_no_data"] is True, "Missing-ticker verdict must have is_no_data=True"
    assert verdict.get("report") is None, "Missing-ticker verdict must have report=None"
    assert verdict["rationale"] == "no verdict from LLM", (
        "Missing-ticker rationale must equal 'no verdict from LLM' exactly "
        "(downstream consumers may key-match on this string)"
    )

    # Sanity-check that the analyst and ticker are correctly attached.
    assert ev["analyst"] == ANALYST
    assert ev["ticker"] == MISSING_TICKER


def test_joiner_present_ticker_is_unaffected() -> None:
    """Tickers that the LLM did emit should not be altered by the no-data path.

    This guards against an accidental over-broad condition that replaces
    valid LLM verdicts with no-data shells.
    """
    state = _make_state(
        tickers=[TICKER, MISSING_TICKER],
        verdicts=[PRESENT_VERDICT],
    )
    ctx = SimpleNamespace(state=state)

    callback = make_evidence_callback(
        analyst=ANALYST,
        extractor=_extractor,
        verdicts_state_key=f"{ANALYST}_verdicts",
    )

    callback(ctx)

    evidence_list: list[dict] = state[f"{ANALYST}_evidence"]
    present_records = [ev for ev in evidence_list if ev["ticker"] == TICKER]
    assert len(present_records) == 1

    ev = present_records[0]
    verdict = ev["verdict"]

    # The LLM-supplied verdict must be preserved verbatim on the key fields.
    assert verdict["is_no_data"] is False
    assert verdict["lean"] == "bullish"
    assert verdict["rationale"] == "strong momentum"
