"""Tier-1 tests for the surface-trace writer."""
from __future__ import annotations

import json
from pathlib import Path

from observability.trace import TraceWriter


def test_snapshot_appends_section():
    """snapshot() appends a labelled JSON section in insertion order."""
    tw = TraceWriter()
    tw.snapshot("01_fetch_news", {"AAPL": {"headlines": []}})
    tw.snapshot("01_fetch_social", {"AAPL": {"reddit": {}}})
    assert list(tw._sections.keys()) == ["01_fetch_news", "01_fetch_social"]


def test_llm_pair_writes_in_and_out_sections():
    """llm_pair writes label_in and label_out adjacent."""
    tw = TraceWriter()
    tw.llm_pair("03_fundamental_llm", "PROMPT TEXT", "RESPONSE TEXT", model="gemini-2.5-flash-lite")
    assert "03_fundamental_llm_in" in tw._sections
    assert "03_fundamental_llm_out" in tw._sections


def test_finalise_writes_json(tmp_path: Path):
    """finalise() writes a single JSON document with all sections."""
    tw = TraceWriter()
    tw.snapshot("01_x", {"a": 1})
    out = tmp_path / "trace.json"
    tw.finalise(out)
    body = json.loads(out.read_text())
    assert body["01_x"] == {"data": {"a": 1}}
