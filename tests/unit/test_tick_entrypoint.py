"""Unit tests for tick.py — no LLM calls."""
import importlib


def test_tick_module_importable():
    import orchestrator.tick
    assert hasattr(orchestrator.tick, "run_once")


def test_run_once_is_coroutine():
    import inspect
    from orchestrator.tick import run_once
    assert inspect.iscoroutinefunction(run_once)
