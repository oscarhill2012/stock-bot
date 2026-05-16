"""Sentinel marker for upstream rows that lack a usable timestamp.

When a news article, filing, or insider-trade row arrives from upstream
without a parseable date/time field, the provider stamps the field with
``MISSING_TIMESTAMP`` rather than substituting ``datetime.now(UTC)``.
The cache writer then *skips* the row (with a structured log line) so it
never enters the PIT-filtered dataset.

Using a sentinel keeps the data model strongly typed (``datetime`` not
``datetime | None``) while making the missing-data path explicit and
auditable.  The audit log (``scripts.backtest_audit_tick``) surfaces a
per-domain count of skipped rows so a reviewer can decide whether
upstream coverage is acceptable for the window.
"""
from __future__ import annotations

from datetime import UTC, datetime

# Year 1 is unambiguous: no real publishedDate / filedAt / transactedAt
# can ever resolve to AD 1 Jan 1, 00:00:00 UTC, and downstream PIT
# filters compare against any plausible ``as_of`` so the sentinel always
# falls outside the window.  Using a real ``datetime`` rather than
# ``None`` keeps Pydantic schemas tight (``datetime`` not
# ``datetime | None``) — only the model's writer-side check needs to know.
MISSING_TIMESTAMP: datetime = datetime(1, 1, 1, tzinfo=UTC)


def is_missing_timestamp(value: datetime | None) -> bool:
    """Return ``True`` iff ``value`` is the missing-timestamp sentinel.

    Parameters
    ----------
    value:
        Timestamp to inspect.  ``None`` returns ``True`` for callers
        that haven't migrated to the sentinel yet.

    Returns
    -------
    bool
        ``True`` when the value is the documented sentinel (or ``None``).
    """
    if value is None:
        return True
    return value == MISSING_TIMESTAMP
