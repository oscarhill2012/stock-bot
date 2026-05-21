"""Unit tests for ``observability.drain.drain_tick``.

Pin the per-tick output contract: three JSON files appear under
``logs/``, ``traces/``, and ``metrics/`` relative to the supplied obs
directory, named ``<tick_slug>.json``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from opentelemetry import metrics, trace

from observability.drain import drain_tick
from observability.otel_setup import _reset_for_tests, install_observability


def test_drain_writes_three_files_named_by_tick_slug(tmp_path: Path):
    """One tick → three files at the agreed paths and filenames."""
    _reset_for_tests()
    handles = install_observability(service_name="drain-test")

    # Emit one of each artefact type so all three files have content.
    tracer = trace.get_tracer("test")
    with tracer.start_as_current_span("invoke_agent"):
        pass

    meter = metrics.get_meter("test")
    hist  = meter.create_histogram("gen_ai.agent.invocation.duration")
    hist.record(42.0)

    logging.getLogger("stockbot.test.drain").info("hello")

    drain_tick(handles, tmp_path, tick_slug="t-42", tick_id="logical-42")

    logs_path    = tmp_path / "logs"    / "t-42.json"
    traces_path  = tmp_path / "traces"  / "t-42.json"
    metrics_path = tmp_path / "metrics" / "t-42.json"

    assert logs_path.exists()
    assert traces_path.exists()
    assert metrics_path.exists()


def test_drained_files_embed_tick_id(tmp_path: Path):
    """All three drained files must record the supplied ``tick_id`` for context."""
    _reset_for_tests()
    handles = install_observability()

    drain_tick(handles, tmp_path, tick_slug="t0", tick_id="logical-tick-id")

    for sub in ("logs", "traces", "metrics"):
        payload = json.loads((tmp_path / sub / "t0.json").read_text())
        assert payload["tick_id"] == "logical-tick-id", f"{sub}/t0.json missing tick_id"


def test_drain_swallows_writer_errors(tmp_path: Path, monkeypatch):
    """A failure in one writer must not block the others."""
    _reset_for_tests()
    handles = install_observability()

    def boom(*_args, **_kwargs):
        """Drop-in replacement that always raises."""
        raise RuntimeError("simulated drain failure")

    monkeypatch.setattr(handles.span_exporter, "drain_to_file", boom)

    # Should NOT raise — drain_tick logs and continues.
    drain_tick(handles, tmp_path, tick_slug="t0", tick_id="x")

    # logs/ and metrics/ still produced even though traces/ blew up.
    assert (tmp_path / "logs" / "t0.json").exists()
    assert (tmp_path / "metrics" / "t0.json").exists()
    assert not (tmp_path / "traces" / "t0.json").exists()
