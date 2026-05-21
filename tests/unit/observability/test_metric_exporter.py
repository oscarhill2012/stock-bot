"""Unit tests for ``observability.exporters.TickBufferedMetricExporter``.

Exercises the exporter through a real MeterProvider — that's the only
honest way to confirm the histogram serialisation works, because the
shape of the ``MetricsData`` snapshot is non-trivial and easy to
mis-stub.
"""
from __future__ import annotations

import json
from pathlib import Path

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from observability.exporters import TickBufferedMetricExporter


def _make_meter_provider_and_exporter() -> tuple[MeterProvider, TickBufferedMetricExporter, PeriodicExportingMetricReader]:
    """Wire a fresh exporter behind a long-interval reader for tests.

    Tests call ``reader.force_flush()`` explicitly to push pending data,
    so the periodic timer being effectively disabled (1-hour interval) is
    fine and keeps the test fast.
    """
    exporter = TickBufferedMetricExporter()
    reader   = PeriodicExportingMetricReader(exporter, export_interval_millis=3_600_000)
    provider = MeterProvider(metric_readers=[reader])

    return provider, exporter, reader


def test_export_accumulates_histograms(tmp_path: Path):
    """Recorded histogram values land in the drained JSON keyed by metric name."""
    provider, exporter, reader = _make_meter_provider_and_exporter()
    meter = provider.get_meter("test")
    hist  = meter.create_histogram("gen_ai.agent.invocation.duration", unit="ms")

    hist.record(123.4, attributes={"gen_ai.agent.name": "news"})
    hist.record(567.8, attributes={"gen_ai.agent.name": "strategist"})

    reader.force_flush(timeout_millis=5_000)

    out = tmp_path / "metrics.json"
    exporter.drain_to_file(out, tick_id="tick-0")
    payload = json.loads(out.read_text())

    assert payload["tick_id"] == "tick-0"

    points = payload["histograms"]["gen_ai.agent.invocation.duration"]

    by_agent = {p["attrs"]["gen_ai.agent.name"]: p for p in points}

    assert by_agent["news"]["sum"]        == 123.4
    assert by_agent["news"]["count"]      == 1
    assert by_agent["strategist"]["sum"]  == 567.8

    provider.shutdown()


def test_drain_resets_buffer(tmp_path: Path):
    """A second drain immediately after the first sees no leftover snapshots."""
    provider, exporter, reader = _make_meter_provider_and_exporter()
    meter = provider.get_meter("test")
    hist  = meter.create_histogram("gen_ai.tool.execution.duration")

    hist.record(10.0)
    reader.force_flush()
    exporter.drain_to_file(tmp_path / "t0.json")

    # Drain again immediately — no new data has been pushed, so the snapshot
    # buffer should be empty (cumulative counters re-emit, but only after a
    # subsequent reader push).
    exporter.drain_to_file(tmp_path / "t1.json")

    second = json.loads((tmp_path / "t1.json").read_text())
    assert second["histograms"] == {}

    provider.shutdown()


def test_drain_creates_parent_dirs(tmp_path: Path):
    """The exporter must mkdir parents like the span exporter does."""
    exporter = TickBufferedMetricExporter()
    out = tmp_path / "nested" / "metrics.json"

    exporter.drain_to_file(out)

    assert out.exists()
