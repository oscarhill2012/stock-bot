"""S4 — reporting.py uses startswith() against ADK span names.

ADK emits spans named ``generate_content <model_id>`` and
``invoke_agent <agent_name>``; the previous ``if name == "generate_content"``
exact-match rejected every span.  Token counters showed 0/0/0 and the
per-agent latency section was empty despite both being populated in
``obs/traces/*.json``.

This module pins the two prefix-match contracts via the
``_aggregate_obs_artefacts`` reader.
"""
from __future__ import annotations

import json
from pathlib import Path


def _write_trace(p: Path, *, spans: list[dict]) -> None:
    """Materialise one obs/traces/*.json file with the supplied spans."""

    p.write_text(json.dumps({"spans": spans}), encoding="utf-8")


def test_generate_content_with_model_suffix_is_counted(tmp_path: Path) -> None:
    """A span named ``generate_content gemini-2.5-flash-lite`` is counted."""

    from backtest.reporting import _aggregate_obs_artefacts

    obs_dir = tmp_path / "obs"
    (obs_dir / "traces").mkdir(parents=True)
    _write_trace(
        obs_dir / "traces" / "tick.json",
        spans=[
            {
                "name":       "generate_content gemini-2.5-flash-lite",
                "attributes": {
                    "gen_ai.usage.input_tokens":  1543,
                    "gen_ai.usage.output_tokens": 88,
                },
                "duration_ms": 12_300,
            },
        ],
    )

    agg = _aggregate_obs_artefacts(obs_dir)

    assert agg is not None
    assert agg["tokens"]["input"]  == 1543
    assert agg["tokens"]["output"] == 88
    assert agg["tokens"]["total"]  == 1631


def test_invoke_agent_with_name_suffix_is_counted(tmp_path: Path) -> None:
    """An ``invoke_agent FundamentalAnalyst_AAPL`` span is grouped by agent."""

    from backtest.reporting import _aggregate_obs_artefacts

    obs_dir = tmp_path / "obs"
    (obs_dir / "traces").mkdir(parents=True)
    _write_trace(
        obs_dir / "traces" / "tick.json",
        spans=[
            {
                "name":       "invoke_agent FundamentalAnalyst_AAPL",
                "attributes": {"gen_ai.agent.name": "FundamentalAnalyst_AAPL"},
                "duration_ms": 11_500,
            },
            {
                "name":       "invoke_agent FundamentalAnalyst_AAPL",
                "attributes": {"gen_ai.agent.name": "FundamentalAnalyst_AAPL"},
                "duration_ms": 12_500,
            },
        ],
    )

    agg = _aggregate_obs_artefacts(obs_dir)

    assert agg is not None
    bucket = agg["agent_latency_ms"]["FundamentalAnalyst_AAPL"]
    assert bucket["count"] == 2
    assert bucket["min"]   == 11_500
    assert bucket["max"]   == 12_500
