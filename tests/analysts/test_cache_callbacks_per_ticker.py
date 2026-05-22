"""Per-ticker cache callback contract — one ticker per LlmAgent, one verdict per response."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from contract.evidence import TickerVerdict


def test_before_single_ticker_hit_returns_single_verdict_llm_response(tmp_path, monkeypatch):
    """A cache hit on the single bound ticker returns an LlmResponse whose
    text is a valid TickerVerdict JSON (NOT a VerdictBatch wrapper)."""

    # Arrange: pre-populate the cache for ticker AAPL.
    from agents.analysts.report_cache import write_cache

    # D1.1: report is required for non-no-data verdicts; include it in the cached entry.
    write_cache(
        tmp_path, "news", "AAPL",
        input_hash="hash-1",
        prompt_version="2026-05-21-a",
        verdict={
            "ticker":      "AAPL",
            "lean":        "bullish",
            "magnitude":   0.7,
            "confidence":  0.8,
            "rationale":   "Strong quarter",
            "key_factors": ["catalyst:earnings", "direction:positive"],
            "is_no_data":  False,
            "report": {
                "summary": "Strong quarterly earnings with positive direction.",
                "drivers": [
                    {"name": "catalyst:earnings",  "direction": "bull", "weight": 0.6, "body": "Earnings beat expectations."},
                    {"name": "direction:positive", "direction": "bull", "weight": 0.4, "body": "Positive price direction confirmed."},
                ],
            },
        },
        report=None,
        # originating_as_of omitted — defaults to None; write_cache expects
        # a datetime object, not a string, so we leave it out here.
    )

    # Build the per-ticker callbacks with cache directory pointed at tmp_path.
    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path))),
    )

    before_cb, _after_cb = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = "2026-05-21-a",
        data_state_key     = "temp:news_data",
        verdicts_state_key = "temp:news_verdict_AAPL",
        ticker             = "AAPL",
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: "hash-1",
        trace_label        = None,
    )

    state = {
        "tickers":       ["AAPL"],
        "temp:news_data": {"AAPL": {"news": []}},
    }
    callback_context = MagicMock(state=state)

    response = before_cb(callback_context, MagicMock())

    # Result must be a valid LlmResponse whose text parses as a TickerVerdict.
    assert response is not None
    text    = response.content.parts[0].text
    parsed  = json.loads(text)
    verdict = TickerVerdict.model_validate(parsed)
    assert verdict.ticker == "AAPL"
    assert verdict.lean   == "bullish"


def test_before_single_ticker_miss_returns_none(tmp_path, monkeypatch):
    """A cache miss returns None — the LLM runs."""

    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path))),
    )

    before_cb, _ = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = "2026-05-21-a",
        data_state_key     = "temp:news_data",
        verdicts_state_key = "temp:news_verdict_AAPL",
        ticker             = "AAPL",
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: "hash-novel",
        trace_label        = None,
    )

    state = {"temp:news_data": {"AAPL": {"news": []}}}
    callback_context = MagicMock(state=state)

    assert before_cb(callback_context, MagicMock()) is None


def test_after_writes_single_verdict_to_cache(tmp_path, monkeypatch):
    """The after-callback parses a single-verdict LLM response and writes one
    cache entry — NOT a {verdicts: [...]} wrapper."""

    monkeypatch.setattr(
        "agents.analysts.cache_callbacks.get_analysts_config",
        lambda: MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path))),
    )

    _before, after_cb = make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = "2026-05-21-a",
        data_state_key     = "temp:news_data",
        verdicts_state_key = "temp:news_verdict_AAPL",
        ticker             = "AAPL",
        output_schema      = TickerVerdict,
        hash_inputs        = lambda d: "hash-fresh",
        trace_label        = None,
    )

    # Synthetic LLM response — a single TickerVerdict JSON, not a batch.
    fake_text = json.dumps({
        "ticker": "AAPL", "lean": "bearish", "magnitude": 0.4,
        "confidence": 0.6, "rationale": "Macro headwinds",
        "key_factors": ["catalyst:macro"], "is_no_data": False,
    })
    llm_response = MagicMock()
    llm_response.content.parts = [MagicMock(text=fake_text)]

    # as_of omitted intentionally — write_cache expects a datetime object or None;
    # in production state["as_of"] is always a real datetime.  Omitting it here
    # keeps the test free of the datetime import and matches the None default.
    state = {"temp:news_data": {"AAPL": {"news": []}}}
    callback_context = MagicMock(state=state)

    result = after_cb(callback_context, llm_response)

    # Returns None (no short-circuit), but a cache entry now exists for AAPL.
    assert result is None

    from agents.analysts.report_cache import read_cache

    hit = read_cache(tmp_path, "news", "AAPL",
                     input_hash="hash-fresh", prompt_version="2026-05-21-a")
    assert hit is not None
    assert hit["verdict"]["lean"] == "bearish"
