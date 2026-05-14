"""Regression tests for the shared LLM trace callback utility.

Covers the Phase 5 trace-fidelity fix: the captured prompt MUST include both
the system instruction (where ``{news_context}`` is filled) AND the user-side
contents. The pre-fix helper only captured ``llm_request.contents`` and
silently dropped the system instruction.
"""
from __future__ import annotations

from types import SimpleNamespace

from observability.trace import TraceWriter, make_llm_trace_callbacks


class _FakeState(dict):
    """Dict-like state object with the same ``.get`` interface ADK uses."""


def _fake_part(text: str) -> SimpleNamespace:
    """Build a fake LlmRequest content part exposing a ``.text`` attribute."""
    return SimpleNamespace(text=text)


def _fake_content(text: str) -> SimpleNamespace:
    """Build a fake LlmRequest content with a list of parts."""
    return SimpleNamespace(parts=[_fake_part(text)])


def _fake_request(system_text: str, user_text: str) -> SimpleNamespace:
    """Build a fake LlmRequest with both system instruction and user contents."""
    config = SimpleNamespace(system_instruction=_fake_content(system_text))
    return SimpleNamespace(config=config, contents=[_fake_content(user_text)])


def _fake_response(text: str) -> SimpleNamespace:
    """Build a fake LlmResponse with a single text part."""
    return SimpleNamespace(content=_fake_content(text))


def test_before_callback_captures_system_and_user_text() -> None:
    """The captured prompt must concatenate system + user under labelled headings."""
    tw = TraceWriter()
    state = _FakeState({"_trace": tw})
    ctx = SimpleNamespace(state=state)

    before, _after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    before(ctx, _fake_request(system_text="SYSTEM:Articles for AAPL", user_text="USER:Run tick"))

    captured = tw._sections["03_news_llm_in"]["prompt"]
    assert "=== system ===" in captured
    assert "SYSTEM:Articles for AAPL" in captured
    assert "=== user ===" in captured
    assert "USER:Run tick" in captured


def test_after_callback_overwrites_pending_marker() -> None:
    """After-callback replaces the ``(pending)`` placeholder with the model response."""
    tw = TraceWriter()
    state = _FakeState({"_trace": tw})
    ctx = SimpleNamespace(state=state)

    before, after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    before(ctx, _fake_request("sys", "usr"))
    after(ctx, _fake_response("VERDICT_JSON"))

    out_section = tw._sections["03_news_llm_out"]
    assert out_section["response"] == "VERDICT_JSON"
    assert out_section["model"] == "gemini-test"


def test_callbacks_are_noops_without_trace_writer() -> None:
    """No trace writer in state -> both callbacks return without raising."""
    state = _FakeState()
    ctx = SimpleNamespace(state=state)

    before, after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    assert before(ctx, _fake_request("sys", "usr")) is None
    assert after(ctx, _fake_response("out")) is None


def test_after_callback_without_before_is_safe() -> None:
    """After-callback runs even if the before-callback never fired (orphan ordering)."""
    tw = TraceWriter()
    state = _FakeState({"_trace": tw})
    ctx = SimpleNamespace(state=state)

    _before, after = make_llm_trace_callbacks("03_news_llm", model="gemini-test")
    after(ctx, _fake_response("only-after"))

    out_section = tw._sections["03_news_llm_out"]
    assert out_section["response"] == "only-after"
    assert out_section["model"] == "gemini-test"
