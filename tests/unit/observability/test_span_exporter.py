"""Unit tests for ``observability.exporters.TickBufferedSpanExporter``.

Pin the buffer-and-drain contract: spans accumulate via ``export``,
``drain_to_file`` writes a JSON document in the agreed OTEL-shaped schema,
and the buffer resets after each drain so successive ticks are
self-contained.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace.export import SpanExportResult

from observability.exporters import TickBufferedSpanExporter


def _fake_span(
    *,
    name:      str = "test_span",
    span_id:   int = 0xABCDEF,
    trace_id:  int = 0x12345678,
    parent_id: int | None = None,
    attrs:     dict | None = None,
):
    """Build a stub object that satisfies the ReadableSpan duck-type our exporter reads.

    The exporter only touches a small subset of ReadableSpan's surface
    (``get_span_context``, ``parent``, ``name``, ``attributes``, ``kind``,
    ``status``, ``start_time``, ``end_time``), so a hand-rolled stub keeps
    the test free of OTEL SDK plumbing.
    """
    from types import SimpleNamespace

    ctx     = SimpleNamespace(span_id=span_id, trace_id=trace_id)
    parent  = SimpleNamespace(span_id=parent_id) if parent_id is not None else None
    status  = SimpleNamespace(status_code=SimpleNamespace(name="OK"))
    kind    = SimpleNamespace(name="INTERNAL")

    span = SimpleNamespace(
        name        = name,
        attributes  = attrs or {},
        kind        = kind,
        status      = status,
        parent      = parent,
        start_time  = 1_000_000_000,  # 1 s
        end_time    = 1_500_000_000,  # 1.5 s
    )
    span.get_span_context = lambda: ctx
    return span


def test_export_appends_to_buffer_and_returns_success():
    """``export`` must accumulate spans and report success."""
    exporter = TickBufferedSpanExporter()

    result = exporter.export([_fake_span(name="a"), _fake_span(name="b")])

    assert result is SpanExportResult.SUCCESS
    assert len(exporter._buffer) == 2


def test_drain_to_file_writes_expected_json_shape(tmp_path: Path):
    """The drained JSON must carry ``tick_id`` + a list of spans in OTEL shape."""
    exporter = TickBufferedSpanExporter()
    exporter.export([
        _fake_span(
            name      = "generate_content",
            span_id   = 0xAA,
            parent_id = 0xBB,
            attrs     = {"gen_ai.usage.input_tokens": 1234},
        ),
    ])

    out = tmp_path / "spans.json"
    exporter.drain_to_file(out, tick_id="my-tick")

    payload = json.loads(out.read_text())

    assert payload["tick_id"] == "my-tick"
    assert len(payload["spans"]) == 1

    span = payload["spans"][0]

    assert span["name"]           == "generate_content"
    assert span["span_id"]        == "00000000000000aa"
    assert span["parent_span_id"] == "00000000000000bb"
    assert span["attributes"]     == {"gen_ai.usage.input_tokens": 1234}
    assert span["duration_ms"]    == pytest.approx(500.0)
    assert span["status"]         == "OK"


def test_drain_resets_buffer_so_next_tick_starts_fresh(tmp_path: Path):
    """Successive drains must not bleed spans across ticks."""
    exporter = TickBufferedSpanExporter()

    exporter.export([_fake_span(name="t0")])
    exporter.drain_to_file(tmp_path / "t0.json", tick_id="t0")

    exporter.export([_fake_span(name="t1")])
    exporter.drain_to_file(tmp_path / "t1.json", tick_id="t1")

    t0_payload = json.loads((tmp_path / "t0.json").read_text())
    t1_payload = json.loads((tmp_path / "t1.json").read_text())

    assert [s["name"] for s in t0_payload["spans"]] == ["t0"]
    assert [s["name"] for s in t1_payload["spans"]] == ["t1"]


def test_drain_creates_parent_directories(tmp_path: Path):
    """Drain must create any missing parent directories rather than crashing."""
    exporter = TickBufferedSpanExporter()
    exporter.export([_fake_span()])

    out = tmp_path / "nested" / "deeply" / "spans.json"
    exporter.drain_to_file(out)

    assert out.exists()


def test_drain_empty_buffer_writes_empty_list(tmp_path: Path):
    """An empty tick should still produce a well-formed JSON file."""
    exporter = TickBufferedSpanExporter()

    out = tmp_path / "empty.json"
    exporter.drain_to_file(out, tick_id="quiet-tick")

    payload = json.loads(out.read_text())

    assert payload == {"tick_id": "quiet-tick", "spans": []}
