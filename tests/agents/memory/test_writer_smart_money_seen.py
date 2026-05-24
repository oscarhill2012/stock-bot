"""MemoryWriter.smart_money_seen reflects new state[smart_money_evidence] shape."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.memory.writer import MemoryWriter


@pytest.mark.asyncio
async def test_smart_money_seen_true_when_real_evidence(monkeypatch):
    """smart_money_seen is True when at least one evidence row has is_no_data=False."""
    writer = MemoryWriter()
    state = {
        "strategist_decision": {
            "decision_tag": "test",
            "reasoning": "x",
            "thesis": "t",
        },
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
        "smart_money_evidence": [
            {"ticker": "AAPL", "verdict": {"is_no_data": False, "lean": "bullish",
                                            "magnitude": 0.4, "confidence": 0.5,
                                            "rationale": "x", "key_factors": []}},
        ],
    }
    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"

    # Use a proper async helper to avoid the asyncio.sleep(result=) incompatibility
    # in Python 3.10+ (asyncio.sleep does not accept a result= keyword there).
    import agents.memory.writer as W

    async def _fake_detect(*a, **kw):
        return False

    monkeypatch.setattr(W, "detect_repeat", _fake_detect)
    monkeypatch.setattr(W, "embed", lambda *a, **kw: [0.0])

    async for _ in writer._run_async_impl(ctx):
        pass
    assert state["memory_buffer"][-1]["smart_money_seen"] is True


@pytest.mark.asyncio
async def test_smart_money_seen_false_when_only_no_data(monkeypatch):
    """smart_money_seen is False when all evidence rows have is_no_data=True."""
    writer = MemoryWriter()
    state = {
        "strategist_decision": {
            "decision_tag": "test",
            "reasoning": "x",
            "thesis": "t",
        },
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
        "smart_money_evidence": [
            {"ticker": "AAPL", "verdict": {"is_no_data": True, "lean": "neutral",
                                            "magnitude": 0.0, "confidence": 0.0,
                                            "rationale": "no data", "key_factors": []}},
        ],
    }
    ctx = MagicMock()
    ctx.session.state = state
    ctx.invocation_id = "test-invocation"

    import agents.memory.writer as W

    async def _fake_detect(*a, **kw):
        return False

    monkeypatch.setattr(W, "detect_repeat", _fake_detect)
    monkeypatch.setattr(W, "embed", lambda *a, **kw: [0.0])

    async for _ in writer._run_async_impl(ctx):
        pass
    assert state["memory_buffer"][-1]["smart_money_seen"] is False
