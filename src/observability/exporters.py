"""In-memory OTEL exporters drained per tick to JSON files.

ADK Python (>= 1.32) natively emits the four GenAI spans
(``invoke_agent``, ``invoke_workflow``, ``execute_tool``,
``generate_content``) and the five GenAI histograms
(``gen_ai.agent.invocation.duration``, ``gen_ai.tool.execution.duration``,
``gen_ai.agent.request.size``, ``gen_ai.agent.response.size``,
``gen_ai.agent.workflow.steps``).  Rather than re-deriving any of that data
from callbacks, we plug custom in-memory exporters into the OTEL SDK,
let ADK populate them as a side-effect of the run, and drain them to disk
at the end of every tick.

The buffer-then-drain shape gives us per-tick file boundaries while still
using ADK's native emission as the ground truth.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

# ── Span exporter ──────────────────────────────────────────────────────────────


class TickBufferedSpanExporter(SpanExporter):
    """Buffer ReadableSpan objects in memory; drain on demand to a JSON file.

    Designed to be paired with a synchronous ``SimpleSpanProcessor`` so spans
    land in the buffer immediately when they close (no async batching delay
    that would let spans from tick N spill into tick N+1's file).

    The drain output mirrors the OTEL GenAI semantic-convention shape so a
    future move to Phoenix / Jaeger is a config flip — we just register a real
    OTLP exporter instead of this one.

    Usage::

        exporter = TickBufferedSpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        # ... run a tick ...
        exporter.drain_to_file(Path("runs/X/obs/traces/0.json"))
    """

    def __init__(self) -> None:
        """Initialise an empty span buffer."""
        super().__init__()

        # ReadableSpan instances accumulate here in the order their spans close.
        # SimpleSpanProcessor calls ``export`` synchronously on every ``end()``.
        self._buffer: list[ReadableSpan] = []

    # ── SpanExporter contract ────────────────────────────────────────────────

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        """Append spans to the buffer; always succeed.

        Parameters
        ----------
        spans:
            The closed spans handed to us by the processor.

        Returns
        -------
        SpanExportResult.SUCCESS
            We never fail — the OTEL SDK would otherwise log warnings.
        """
        self._buffer.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        """OTEL contract — nothing to release here."""

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """OTEL contract — buffer is always up to date, so nothing to flush.

        Parameters
        ----------
        timeout_millis:
            Ignored — we never block.

        Returns
        -------
        bool
            Always ``True``.
        """
        return True

    # ── per-tick drain ───────────────────────────────────────────────────────

    def drain_to_file(self, path: Path, *, tick_id: str | None = None) -> None:
        """Serialise the buffered spans to ``path`` and reset the buffer.

        Parameters
        ----------
        path:
            Destination JSON file.  Parent directories are created.
        tick_id:
            Optional tick identifier embedded in the document for convenience
            when reading a file standalone.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        document: dict[str, Any] = {
            "tick_id": tick_id,
            "spans":   [_serialise_span(span) for span in self._buffer],
        }

        path.write_text(json.dumps(document, indent=2, default=str))

        # Reset for the next tick.  We re-bind rather than ``clear()`` so any
        # caller that captured ``self._buffer`` for read-only inspection sees
        # a stable snapshot.
        self._buffer = []


def _serialise_span(span: ReadableSpan) -> dict[str, Any]:
    """Convert a ReadableSpan to a JSON-safe dict in the OTEL GenAI shape.

    Mirrors what an OTLP exporter would emit — span_id, parent_span_id,
    name, attributes, status, start/end nanos, plus a convenience
    ``duration_ms`` field.

    Parameters
    ----------
    span:
        A closed OTEL span captured by the SDK.

    Returns
    -------
    dict
        JSON-serialisable representation.
    """
    ctx        = span.get_span_context()
    parent     = span.parent
    start_ns   = span.start_time or 0
    end_ns     = span.end_time   or start_ns

    # OTEL stores attributes as a ``BoundedAttributes`` instance; convert to a
    # plain dict so ``json.dumps`` is happy.
    attributes = dict(span.attributes) if span.attributes else {}

    return {
        "name":           span.name,
        "span_id":        f"{ctx.span_id:016x}",
        "trace_id":       f"{ctx.trace_id:032x}",
        "parent_span_id": f"{parent.span_id:016x}" if parent else None,
        "kind":           span.kind.name if span.kind else None,
        "status":         span.status.status_code.name if span.status else None,
        "start_time_ns":  start_ns,
        "end_time_ns":    end_ns,
        "duration_ms":    (end_ns - start_ns) / 1_000_000.0,
        "attributes":     attributes,
    }


# ── Metric exporter ────────────────────────────────────────────────────────────


class TickBufferedMetricExporter(MetricExporter):
    """Buffer ``MetricsData`` snapshots in memory; drain on demand to JSON.

    Designed to be paired with a ``PeriodicExportingMetricReader`` whose
    ``force_flush`` is called immediately before each drain so the latest
    reading lands in the buffer before we serialise it.

    The drain output mirrors the OTEL GenAI semantic-convention metric shape
    (one entry per histogram with its data points and attributes).
    """

    def __init__(self) -> None:
        """Initialise an empty metric buffer."""
        # No preferred temporality / aggregation overrides — let the SDK use
        # its defaults (cumulative histograms with explicit buckets).
        super().__init__()

        self._buffer: list[MetricsData] = []

    # ── MetricExporter contract ──────────────────────────────────────────────

    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: Any,
    ) -> MetricExportResult:
        """Append a metrics snapshot to the buffer; always succeed.

        Parameters
        ----------
        metrics_data:
            The current MetricsData snapshot pushed by the reader.
        timeout_millis:
            Ignored — we never block.
        **kwargs:
            Ignored — future OTEL extension hook.

        Returns
        -------
        MetricExportResult.SUCCESS
        """
        self._buffer.append(metrics_data)
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        """OTEL contract — buffer is up to date, nothing to flush.

        Returns
        -------
        bool
            Always ``True``.
        """
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        """OTEL contract — nothing to release."""

    # ── per-tick drain ───────────────────────────────────────────────────────

    def drain_to_file(self, path: Path, *, tick_id: str | None = None) -> None:
        """Serialise buffered MetricsData to ``path`` and reset the buffer.

        We collapse every ``MetricsData`` snapshot in the buffer into a single
        ``histograms`` mapping keyed by metric name.  Data points are flattened
        into a list of ``{attrs, value, count, sum}`` entries.

        Parameters
        ----------
        path:
            Destination JSON file.  Parent directories are created.
        tick_id:
            Optional tick identifier embedded in the document.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        histograms: dict[str, list[dict[str, Any]]] = {}

        for snapshot in self._buffer:
            for resource_metric in snapshot.resource_metrics:
                for scope_metric in resource_metric.scope_metrics:
                    for metric in scope_metric.metrics:
                        # Each metric has a ``data`` attr that holds either a
                        # Histogram, Sum, or Gauge — we serialise whichever is
                        # present.  Histograms are the only type ADK emits for
                        # its native gen_ai.* metrics, but be defensive.
                        entries = histograms.setdefault(metric.name, [])
                        entries.extend(_serialise_metric_data(metric))

        document: dict[str, Any] = {
            "tick_id":    tick_id,
            "histograms": histograms,
        }

        path.write_text(json.dumps(document, indent=2, default=str))

        self._buffer = []


def _serialise_metric_data(metric: Any) -> list[dict[str, Any]]:
    """Flatten a metric's data points into a list of JSON-safe dicts.

    Supports histogram, sum, and gauge data shapes — the structure of each
    follows the OTEL SDK ``opentelemetry.sdk.metrics.export`` types.

    Parameters
    ----------
    metric:
        A ``Metric`` instance whose ``.data`` is a ``Histogram`` / ``Sum`` /
        ``Gauge``.

    Returns
    -------
    list[dict]
        One entry per data point.  Attributes are flattened into ``attrs``
        for ease of post-hoc filtering.
    """
    data   = metric.data
    points = getattr(data, "data_points", []) or []

    rows: list[dict[str, Any]] = []

    for point in points:
        # Attributes can be ``BoundedAttributes`` — convert defensively.
        attrs = dict(point.attributes) if point.attributes else {}

        # Histograms expose ``bucket_counts``, ``sum``, ``count``; gauges
        # / sums expose ``value``.  We capture whichever fields are present
        # rather than branching on the data type — keeps this resilient if
        # OTEL adds new point types.
        row: dict[str, Any] = {
            "attrs":      attrs,
            "start_time_ns": getattr(point, "start_time_unix_nano", None),
            "time_ns":    getattr(point, "time_unix_nano", None),
        }

        # Histogram fields.
        for field in ("count", "sum", "min", "max"):
            value = getattr(point, field, None)
            if value is not None:
                row[field] = value

        # Sum / gauge fields.
        if hasattr(point, "value"):
            row["value"] = point.value

        rows.append(row)

    return rows
