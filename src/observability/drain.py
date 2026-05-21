"""Per-tick drain orchestration for the three observability artefacts.

Called by ``backtest.driver.Driver`` at the end of every tick.  Writes
three files under ``runs/<id>/obs/{logs,traces,metrics}/<tick>.json``.

The drain is best-effort — a failure in serialisation must never bring
down the tick loop.  Errors are logged and swallowed.
"""
from __future__ import annotations

import logging
from pathlib import Path

from observability.otel_setup import ObservabilityHandles

_drain_logger = logging.getLogger(__name__)


def drain_tick(
    handles: ObservabilityHandles,
    obs_dir: Path,
    *,
    tick_slug: str,
    tick_id:   str,
) -> None:
    """Flush spans, metrics, and logs from the buffers to per-tick JSON files.

    Parameters
    ----------
    handles:
        The bundle returned by ``install_observability``.
    obs_dir:
        Run-scoped directory; ``logs/``, ``traces/``, ``metrics/`` subdirs
        are created beneath it.
    tick_slug:
        Filesystem-safe identifier used as the per-tick filename stem
        (e.g. ``"2023-03-10T13-30-00p00-00-open"``).
    tick_id:
        Logical tick identifier embedded in each file for context when
        reading one file in isolation.
    """
    # Force the metric reader to push its latest reading to the exporter so
    # the buffer reflects this tick's invocations.  Without this call the
    # periodic reader would only push on its (1-hour) timer.
    try:
        handles.metric_reader.force_flush(timeout_millis=5_000)
    except Exception:
        _drain_logger.exception("metric_reader.force_flush failed; metrics file may lag a tick")

    logs_path    = obs_dir / "logs"    / f"{tick_slug}.json"
    traces_path  = obs_dir / "traces"  / f"{tick_slug}.json"
    metrics_path = obs_dir / "metrics" / f"{tick_slug}.json"

    # Each writer is independent — one failure must not block the others, so
    # wrap each in its own try/except.
    for writer, path in (
        (handles.log_handler,     logs_path),
        (handles.span_exporter,   traces_path),
        (handles.metric_exporter, metrics_path),
    ):
        try:
            writer.drain_to_file(path, tick_id=tick_id)
        except Exception:
            _drain_logger.exception(
                "drain_to_file failed for %s (path=%s); buffer will be reset on next tick",
                type(writer).__name__,
                path,
            )
