"""Unit tests for ``observability.otel_setup.install_observability``.

Pin the idempotency contract — install can be called repeatedly and only
the first call actually wires providers; the rest return the same handles.
Also verify the log handler is attached to every project namespace so
existing ``logger = logging.getLogger(__name__)`` calls in
``agents.*`` / ``backtest.*`` / ``orchestrator.*`` etc. flow through.
"""
from __future__ import annotations

import logging

from observability.otel_setup import (
    ObservabilityHandles,
    _reset_for_tests,
    get_handles,
    install_observability,
)


def test_returns_observability_handles_bundle():
    """First install returns a populated handle bundle."""
    _reset_for_tests()
    handles = install_observability()

    assert isinstance(handles, ObservabilityHandles)
    assert handles.span_exporter   is not None
    assert handles.metric_exporter is not None
    assert handles.log_handler     is not None
    assert handles.metric_reader   is not None


def test_install_is_idempotent():
    """Second call to install must return the same handles, not new ones."""
    _reset_for_tests()
    first  = install_observability()
    second = install_observability()

    assert first is second
    assert first.log_handler is second.log_handler


def test_get_handles_returns_installed_bundle():
    """``get_handles`` should expose the singleton without re-installing."""
    _reset_for_tests()
    installed = install_observability()

    assert get_handles() is installed


def test_get_handles_returns_none_before_install():
    """Production live ticks that skip install must see ``None``."""
    _reset_for_tests()

    assert get_handles() is None


def test_log_handler_attached_to_project_namespaces():
    """Each top-level project namespace must have the handler attached.

    Ensures records from ``agents.analysts.cache_callbacks`` (via
    ``logger = logging.getLogger(__name__)``) reach the per-tick log buffer.
    """
    _reset_for_tests()
    handles = install_observability()

    for namespace in ("google_adk", "agents", "backtest", "orchestrator", "observability"):
        target = logging.getLogger(namespace)
        assert handles.log_handler in target.handlers, (
            f"log handler not attached to {namespace!r} — child loggers under it "
            f"will not flow into the per-tick logs file"
        )


def test_repeated_install_does_not_attach_duplicate_handlers():
    """Calling install twice in the same process must not double-attach the handler."""
    _reset_for_tests()
    handles = install_observability()
    install_observability()  # second call

    agents_logger = logging.getLogger("agents")
    handler_count = sum(1 for h in agents_logger.handlers if h is handles.log_handler)

    assert handler_count == 1
