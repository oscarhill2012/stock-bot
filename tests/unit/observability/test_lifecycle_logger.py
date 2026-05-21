"""Unit tests for ``observability.otel_setup.AgentLifecycleLogger``.

Pin the contract: one INFO log line per ADK ``invoke_agent`` span end,
nothing else.  We exercise the processor directly with a hand-rolled
``ReadableSpan`` stub — going through a real ``TracerProvider`` would
also work but couples the test to OTEL SDK internals unnecessarily.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from observability.otel_setup import AgentLifecycleLogger


def _fake_span(
    *,
    name:        str = "invoke_agent",
    agent_name:  str | None = "NewsAnalyst",
    start_ns:    int = 1_000_000_000,    # 1 s
    end_ns:      int = 1_500_000_000,    # 1.5 s  → 500 ms duration
):
    """Build a ``ReadableSpan`` duck for the lifecycle logger to consume.

    Only the attributes the processor actually touches are populated.
    """
    attrs = {}
    if agent_name is not None:
        attrs["gen_ai.agent.name"] = agent_name

    return SimpleNamespace(
        name       = name,
        attributes = attrs,
        start_time = start_ns,
        end_time   = end_ns,
    )


def test_emits_one_info_line_per_invoke_agent_span(caplog) -> None:
    """A single closed ``invoke_agent`` span produces one INFO record."""
    caplog.set_level(logging.INFO, logger="stockbot.lifecycle")

    proc = AgentLifecycleLogger()
    proc.on_end(_fake_span(agent_name="NewsAnalyst"))

    records = [r for r in caplog.records if r.name == "stockbot.lifecycle"]

    assert len(records) == 1
    assert records[0].levelname == "INFO"
    assert "NewsAnalyst"        in records[0].getMessage()
    assert "500 ms"             in records[0].getMessage()


def test_ignores_non_invoke_agent_spans(caplog) -> None:
    """``generate_content`` and other span names must produce zero output."""
    caplog.set_level(logging.INFO, logger="stockbot.lifecycle")

    proc = AgentLifecycleLogger()
    proc.on_end(_fake_span(name="generate_content"))
    proc.on_end(_fake_span(name="execute_tool"))
    proc.on_end(_fake_span(name="invoke_workflow"))

    records = [r for r in caplog.records if r.name == "stockbot.lifecycle"]
    assert records == []


def test_missing_agent_name_falls_back_to_unknown(caplog) -> None:
    """A span without ``gen_ai.agent.name`` still logs, with ``<unknown>``."""
    caplog.set_level(logging.INFO, logger="stockbot.lifecycle")

    proc = AgentLifecycleLogger()
    proc.on_end(_fake_span(agent_name=None))

    records = [r for r in caplog.records if r.name == "stockbot.lifecycle"]

    assert len(records) == 1
    assert "<unknown>" in records[0].getMessage()


def test_on_start_is_a_no_op(caplog) -> None:
    """``on_start`` must not emit anything — we only log on end."""
    caplog.set_level(logging.INFO, logger="stockbot.lifecycle")

    proc = AgentLifecycleLogger()
    proc.on_start(_fake_span(), parent_context=None)

    assert [r for r in caplog.records if r.name == "stockbot.lifecycle"] == []
