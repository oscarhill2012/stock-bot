"""Tests for the obs/ aggregation helpers used by ``backtest.reporting``.

Pin the contract between the per-tick observability writers
(``observability.exporters`` + ``observability.log_handler``) and the
end-of-run roll-up surfaced in ``report/metrics.md``.  The aggregator
reads the JSON shape those writers produce — so these tests double as a
schema regression check.
"""
from __future__ import annotations

import json
from pathlib import Path

from backtest.reporting import _aggregate_obs_artefacts, _format_obs_section

# ── Fixture helpers ───────────────────────────────────────────────────────────

def _make_obs_dir(tmp_path: Path) -> Path:
    """Build an empty obs/ directory tree mirroring the runtime layout."""
    obs = tmp_path / "obs"
    (obs / "traces").mkdir(parents=True)
    (obs / "metrics").mkdir(parents=True)
    (obs / "logs").mkdir(parents=True)
    return obs


def _write_trace(
    obs: Path,
    tick_slug: str,
    spans: list[dict],
) -> None:
    """Write a single tick's trace file with the agreed schema."""
    payload = {"tick_id": tick_slug, "spans": spans}
    (obs / "traces" / f"{tick_slug}.json").write_text(json.dumps(payload))


def _write_log(
    obs: Path,
    tick_slug: str,
    events: list[dict],
) -> None:
    """Write a single tick's log file with the agreed schema."""
    payload = {"tick_id": tick_slug, "events": events}
    (obs / "logs" / f"{tick_slug}.json").write_text(json.dumps(payload))


# ── _aggregate_obs_artefacts ──────────────────────────────────────────────────

class TestAggregateObsArtefacts:
    """Tests for ``_aggregate_obs_artefacts`` — the reader half of the contract."""

    def test_returns_none_when_obs_tree_is_empty(self, tmp_path: Path) -> None:
        """An obs/ dir with three empty subdirs should yield no section."""
        obs = _make_obs_dir(tmp_path)

        assert _aggregate_obs_artefacts(obs) is None

    def test_sums_generate_content_token_attributes(self, tmp_path: Path) -> None:
        """Token attributes on ``generate_content`` spans add up across ticks."""
        obs = _make_obs_dir(tmp_path)

        # Two ticks, each with one model call — total input = 300, output = 60.
        _write_trace(obs, "t0", [
            {
                "name": "generate_content",
                "attributes": {
                    "gen_ai.usage.input_tokens":  100,
                    "gen_ai.usage.output_tokens": 20,
                },
            },
        ])
        _write_trace(obs, "t1", [
            {
                "name": "generate_content",
                "attributes": {
                    "gen_ai.usage.input_tokens":  200,
                    "gen_ai.usage.output_tokens": 40,
                },
            },
        ])

        result = _aggregate_obs_artefacts(obs)

        assert result is not None
        assert result["tokens"]["input"]                  == 300
        assert result["tokens"]["output"]                 == 60
        assert result["tokens"]["total"]                  == 360
        assert result["tokens"]["generate_content_spans"] == 2

    def test_per_agent_latency_envelope(self, tmp_path: Path) -> None:
        """``invoke_agent`` spans drive a per-agent count/sum/min/max bucket."""
        obs = _make_obs_dir(tmp_path)

        _write_trace(obs, "t0", [
            {
                "name":        "invoke_agent",
                "duration_ms": 100.0,
                "attributes":  {"gen_ai.agent.name": "news_analyst"},
            },
            {
                "name":        "invoke_agent",
                "duration_ms": 300.0,
                "attributes":  {"gen_ai.agent.name": "news_analyst"},
            },
            {
                "name":        "invoke_agent",
                "duration_ms": 50.0,
                "attributes":  {"gen_ai.agent.name": "strategist"},
            },
        ])

        result = _aggregate_obs_artefacts(obs)

        assert result is not None
        latency = result["agent_latency_ms"]

        assert latency["news_analyst"]["count"] == 2
        assert latency["news_analyst"]["sum"]   == 400.0
        assert latency["news_analyst"]["min"]   == 100.0
        assert latency["news_analyst"]["max"]   == 300.0

        assert latency["strategist"]["count"] == 1
        assert latency["strategist"]["min"]   == 50.0
        assert latency["strategist"]["max"]   == 50.0

    def test_counts_cache_hits_and_misses_from_logs(self, tmp_path: Path) -> None:
        """``report_cache_hit`` and ``report_cache_miss`` log events are tallied."""
        obs = _make_obs_dir(tmp_path)

        _write_log(obs, "t0", [
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_miss", "logger": "agents.analysts.cache_callbacks"},
            # Unrelated log line — must not be counted.
            {"message": "something else",    "logger": "stockbot.other"},
        ])

        result = _aggregate_obs_artefacts(obs)

        assert result is not None
        assert result["cache"]["hits"]   == 2
        assert result["cache"]["misses"] == 1

    def test_retries_counted_by_logger_namespace(self, tmp_path: Path) -> None:
        """Records under ``agents.llm_retry`` are counted as retry events."""
        obs = _make_obs_dir(tmp_path)

        _write_log(obs, "t0", [
            {"message": "retrying", "logger": "agents.llm_retry"},
            {"message": "retrying", "logger": "agents.llm_retry.news"},  # child
            {"message": "noise",    "logger": "google_adk"},             # unrelated
        ])

        result = _aggregate_obs_artefacts(obs)

        assert result is not None
        assert result["retries"] == 2

    def test_lenient_on_malformed_trace_file(self, tmp_path: Path) -> None:
        """A non-JSON file in traces/ must not abort the run."""
        obs = _make_obs_dir(tmp_path)

        (obs / "traces" / "broken.json").write_text("not json at all {{{")
        _write_trace(obs, "t0", [
            {
                "name": "generate_content",
                "attributes": {"gen_ai.usage.input_tokens": 5},
            },
        ])

        result = _aggregate_obs_artefacts(obs)

        # The valid file still contributes.
        assert result is not None
        assert result["tokens"]["input"] == 5


# ── _format_obs_section ───────────────────────────────────────────────────────

class TestFormatObsSection:
    """Tests for the Markdown renderer."""

    def test_section_includes_token_totals_and_cache_rate(self) -> None:
        """The rendered Markdown surfaces token totals and cache hit rate."""
        aggs = {
            "tokens":           {"input": 1000, "output": 200, "total": 1200, "generate_content_spans": 4},
            "agent_latency_ms": {"news_analyst": {"count": 2, "sum": 400.0, "min": 100.0, "max": 300.0}},
            "cache":            {"hits": 3, "misses": 1},
            "retries":          0,
            "ticks_observed":   2,
        }

        out = _format_obs_section(aggs)

        assert "## Pipeline efficiency" in out
        assert "1,000" in out                  # input tokens (with thousands separator)
        assert "1,200" in out                  # total tokens
        assert "3 hits / 4 lookups" in out
        assert "75.0% hit rate"     in out

    def test_per_agent_table_sorts_by_total_time_descending(self) -> None:
        """The heaviest agent (largest total ms) appears first in the table."""
        aggs = {
            "tokens":           {"input": 0, "output": 0, "total": 0, "generate_content_spans": 0},
            "agent_latency_ms": {
                "cheap":      {"count": 1, "sum":   50.0, "min":  50.0, "max":   50.0},
                "expensive":  {"count": 1, "sum": 5000.0, "min": 5000.0, "max": 5000.0},
                "middle":     {"count": 1, "sum":  500.0, "min": 500.0, "max":  500.0},
            },
            "cache":            {"hits": 0, "misses": 0},
            "retries":          0,
            "ticks_observed":   1,
        }

        out = _format_obs_section(aggs)

        # ``expensive`` row must precede ``middle`` which must precede ``cheap``.
        assert out.index("`expensive`") < out.index("`middle`") < out.index("`cheap`")

    def test_zero_cache_lookups_renders_friendly_placeholder(self) -> None:
        """No cache lookups → italic ``no cache lookups recorded`` line."""
        aggs = {
            "tokens":           {"input": 0, "output": 0, "total": 0, "generate_content_spans": 0},
            "agent_latency_ms": {},
            "cache":            {"hits": 0, "misses": 0},
            "retries":          0,
            "ticks_observed":   0,
        }

        out = _format_obs_section(aggs)

        assert "_no cache lookups recorded_" in out
