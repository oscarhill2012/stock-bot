"""Audit-log subsystem — per-tick telemetry (Layer 1) and deep-dump (Layer 2).

Layer 1 (``telemetry``) is always on and writes a ~5 KB JSON record per tick
under ``runs/<id>/audit/<tick-slug>.tick.json``.  Tripwire flags surface
suspected leaks at a glance.

Layer 2 (``deep_dump``, Task 7) is opt-in and re-runs a single tick with
capture enabled on the store itself; the cache store captures every read via
its own ``_audit_*`` API; see ``backtest.cache.store.CachedDataStore``.
Each captured row is re-fetched from upstream for independent verification.

2026-05-26 audit (F-backtest-014) reviewed this subsystem and decided to
keep it as-is — no changes required at this time.
"""
from __future__ import annotations
