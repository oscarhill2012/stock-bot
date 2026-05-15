"""Audit-log subsystem — per-tick telemetry (Layer 1) and deep-dump (Layer 2).

Layer 1 (``telemetry``) is always on and writes a ~5 KB JSON record per tick
under ``runs/<id>/audit/<tick-slug>.tick.json``.  Tripwire flags surface
suspected leaks at a glance.

Layer 2 (``deep_dump``, Task 7) is opt-in and re-runs a single tick with an
``AuditingStore`` decorator that captures every cache read and re-fetches
from upstream for independent verification.
"""
from __future__ import annotations
