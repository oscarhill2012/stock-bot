"""One-time OTEL provider installation for the StockBot observability stack.

Builds a ``TracerProvider`` with a ``SimpleSpanProcessor`` feeding a
``TickBufferedSpanExporter``, and a ``MeterProvider`` with a
``PeriodicExportingMetricReader`` feeding a ``TickBufferedMetricExporter``.
Both are wired as the *global* OTEL providers so ADK's native
instrumentation (``invoke_agent`` / ``generate_content`` spans, the
``gen_ai.*`` histograms) lands in our exporters without any further
configuration.

A stdlib ``logging.Handler`` is also installed on the ``google_adk`` and
``stockbot`` parent loggers so DEBUG-level ADK records (full prompts +
responses, lifecycle events) and our own deliberate log calls are buffered
for per-tick draining.

The setup is idempotent — calling ``install_observability()`` twice in
the same process re-uses the existing handles rather than installing
duplicate providers (which would multiply every emitted span).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from observability.exporters import (
    TickBufferedMetricExporter,
    TickBufferedSpanExporter,
)
from observability.log_handler import TickBufferedLogHandler

# ── Public bundle returned to the driver ──────────────────────────────────────


@dataclass
class ObservabilityHandles:
    """The three exporters/handler the driver needs to drain per tick.

    Attributes
    ----------
    span_exporter:
        Buffers OTEL spans (ADK's native ``invoke_agent`` /
        ``generate_content`` / etc.).
    metric_exporter:
        Buffers OTEL metric snapshots (ADK's native ``gen_ai.*``
        histograms).
    log_handler:
        Buffers ``google_adk.*`` and ``stockbot.*`` log records.
    metric_reader:
        The reader feeding ``metric_exporter`` — driver calls
        ``force_flush`` on it just before draining the exporter so the
        latest reading is captured.
    """

    span_exporter:   TickBufferedSpanExporter
    metric_exporter: TickBufferedMetricExporter
    log_handler:     TickBufferedLogHandler
    metric_reader:   PeriodicExportingMetricReader


# Process-wide singleton.  Idempotency depends on this — re-installing OTEL
# providers in the same process would double-emit every span (the previous
# provider isn't unregistered when ``set_tracer_provider`` is called again).
_HANDLES: ObservabilityHandles | None = None


def install_observability(*, service_name: str = "stockbot") -> ObservabilityHandles:
    """Install OTEL providers + log handler; return the per-tick handles.

    Idempotent — subsequent calls return the same handles without re-wiring
    the global providers.

    Parameters
    ----------
    service_name:
        Resource attribute applied to every emitted span / metric.
        Defaults to ``"stockbot"``.

    Returns
    -------
    ObservabilityHandles
        The driver should call ``drain_to_file`` on each of the three
        exporters/handler at tick end.
    """
    global _HANDLES

    if _HANDLES is not None:
        return _HANDLES

    resource = Resource.create({"service.name": service_name})

    # ── Tracer provider ────────────────────────────────────────────────────
    # ``SimpleSpanProcessor`` exports each span synchronously when it closes.
    # The async ``BatchSpanProcessor`` would batch in a background thread —
    # fine for production OTLP exporters but it would let spans from tick N
    # spill into tick N+1's drain because batches flush on a timer.  We
    # explicitly want one-tick-one-file determinism.
    span_exporter   = TickBufferedSpanExporter()
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # ── Meter provider ─────────────────────────────────────────────────────
    # ``PeriodicExportingMetricReader`` pushes metric snapshots on a timer.
    # We set the interval high (1 hour) because we drain manually via
    # ``force_flush`` at tick end; the periodic push is a safety net rather
    # than the primary path.
    metric_exporter = TickBufferedMetricExporter()
    metric_reader   = PeriodicExportingMetricReader(
        exporter              = metric_exporter,
        export_interval_millis= 3_600_000,  # 1 hour — see comment above
    )
    meter_provider = MeterProvider(
        resource       = resource,
        metric_readers = [metric_reader],
    )
    metrics.set_meter_provider(meter_provider)

    # ── Log handler ────────────────────────────────────────────────────────
    log_handler = TickBufferedLogHandler()

    # Attach to every namespace whose log records we want captured.  The
    # ``google_adk`` parent picks up ADK's framework logs (lifecycle,
    # tool execution, and at DEBUG level the full LLM prompts / responses
    # per https://adk.dev/observability/logging/).  The remaining names are
    # the project's top-level Python packages under ``src/`` — anything
    # ``logger = logging.getLogger(__name__)`` produces in this codebase
    # will fall under one of them.  ``stockbot`` is reserved for any
    # future namespace migration but harmless if currently unused.
    captured_namespaces = (
        "google_adk",
        "stockbot",
        "agents",
        "backtest",
        "orchestrator",
        "observability",
        "data",
        "broker",
        "contract",
        "config",
    )

    for logger_name in captured_namespaces:
        target_logger = logging.getLogger(logger_name)
        target_logger.setLevel(logging.DEBUG)

        # Defensive: if we somehow get called twice and skip the singleton
        # short-circuit (e.g. tests reaching past it), avoid attaching the
        # same handler instance twice to the same logger.
        if log_handler not in target_logger.handlers:
            target_logger.addHandler(log_handler)

    _HANDLES = ObservabilityHandles(
        span_exporter   = span_exporter,
        metric_exporter = metric_exporter,
        log_handler     = log_handler,
        metric_reader   = metric_reader,
    )
    return _HANDLES


def get_handles() -> ObservabilityHandles | None:
    """Return the installed handles, or ``None`` if ``install_observability`` was never called.

    Returns
    -------
    ObservabilityHandles | None
        ``None`` on production (live) ticks where observability is not
        explicitly installed; the bundle otherwise.
    """
    return _HANDLES


def _reset_for_tests() -> None:
    """Drop the singleton handle so a fresh provider can be installed.

    For unit tests only.  Does NOT clean up the global OTEL providers — the
    SDK does not officially support tearing those down, and calling
    ``set_tracer_provider(NoOpTracerProvider())`` would silently silence
    every test that runs after this one in the same process.  Tests that
    need a clean slate should be marked to run in their own subprocess.
    """
    global _HANDLES
    _HANDLES = None
