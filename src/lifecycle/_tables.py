# src/lifecycle/_tables.py
"""Single source of truth for the StockBot table list used by
``lifecycle.initialise._check_live_tables_empty`` and
``lifecycle.hard_reset._row_counts`` / ``_archive_*`` / ``_truncate_live``.

Derived from ``Base.metadata.tables.keys()`` so any ORM table added or
removed in ``src/orchestrator/persistence.py`` is automatically picked
up by both preflight and hard-reset — closing the A-011 silent-failure
where a hand-maintained tuple let preflight pass on stale rows in
ORM tables not listed in the tuple.
"""
from __future__ import annotations

from orchestrator.persistence import Base

# Tuple (not a set) so iteration order is stable and the archive /
# truncate operations process tables in the same deterministic order
# every run.  ``Base.metadata.tables`` is an ``immutabledict`` whose
# ordering reflects ORM-declaration order in persistence.py.
STOCKBOT_TABLES: tuple[str, ...] = tuple(Base.metadata.tables.keys())
