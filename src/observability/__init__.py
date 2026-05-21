"""Observability primitives.

Two coexisting layers (the OTEL layer is additive — TraceWriter remains
the source of truth for the existing domain-labelled per-tick traces
until its 15+ call sites are migrated in a follow-up):

* **TraceWriter** (``observability.trace``) — manual per-boundary JSON
  snapshots collected via ``_trace_maybe(state, ...)`` hooks scattered
  across the agents.  Captures domain-shaped data (e.g. ``"01_fetch_news"``,
  ``"06_risk_gate_out"``).
* **OTEL stack** (``observability.otel_setup``, ``observability.exporters``,
  ``observability.log_handler``, ``observability.drain``) — taps ADK's
  native OTEL emission (spans, metrics) plus the ``google_adk.*``
  ``logging`` namespace.  Three per-tick JSON files under
  ``runs/<id>/obs/{logs,traces,metrics}/<tick>.json`` aligned with ADK's
  own three-pillar taxonomy (https://adk.dev/observability/).
"""
