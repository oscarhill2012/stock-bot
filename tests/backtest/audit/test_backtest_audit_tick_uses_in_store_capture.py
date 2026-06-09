"""The deep-dump CLI must capture rows via CachedDataStore._audit_* —
not via a separate decorator.  Pinning the surface prevents the
two-mechanism split from reappearing.
"""
from __future__ import annotations

import importlib

import pytest


def test_backtest_audit_tick_does_not_reference_auditing_store():
    """The audit CLI source must not mention AuditingStore at all —
    not as a module-level import, and not as a local import inside a
    function body.  Reading the source text (rather than hasattr) catches
    both, since the CLI already uses function-local imports elsewhere.
    """
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("scripts.backtest_audit_tick")
    assert spec is not None and spec.origin is not None
    source = Path(spec.origin).read_text(encoding="utf-8")
    assert "AuditingStore" not in source, (
        "scripts.backtest_audit_tick still references AuditingStore — "
        "use CachedDataStore._audit_enable_capture / _audit_drain_reads."
    )


def test_auditing_store_module_is_gone():
    """The redundant module must be deleted, not just unreferenced."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backtest.audit.auditing_store")
