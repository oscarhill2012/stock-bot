"""Unit tests for ``collapse_repeat_buffer_entries`` in StrategistContextShim.

The helper collapses consecutive runs of ``is_repeat=True`` / no-execution
entries into a single summary line while preserving every action entry
(``executions_count > 0``) verbatim and in chronological order.

These tests assert on concrete content — presence of action entries, absence
of spurious lines for collapsed runs — so silent-degradation bugs (entries
dropped, order scrambled, action entries swallowed into the repeat summary)
surface immediately.
"""
from __future__ import annotations

from agents.strategist.context_shim import collapse_repeat_buffer_entries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(
    tag: str                 = "no_action",
    summary: str             = "no change",
    is_repeat: bool          = False,
    executions_count: int    = 0,
) -> dict:
    """Build a minimal raw buffer-entry dict for testing.

    Parameters
    ----------
    tag:
        The ``decision_tag`` field.
    summary:
        The ``reasoning_summary`` field.
    is_repeat:
        Whether this entry was flagged as a semantic repeat by the dedup pass.
    executions_count:
        Number of broker orders that filled on this tick.

    Returns
    -------
    dict
        Buffer-entry dict matching the shape MemoryWriter stores in state.
    """
    return {
        "decision_tag":     tag,
        "reasoning_summary": summary,
        "is_repeat":         is_repeat,
        "executions_count":  executions_count,
    }


# ---------------------------------------------------------------------------
# Empty / trivial cases
# ---------------------------------------------------------------------------

def test_empty_buffer_returns_sentinel() -> None:
    """An empty buffer must return the explicit empty-state sentinel.

    The sentinel distinguishes "no prior ticks" from a missing key, so the
    LLM does not mistake a cold-start run for a run with no memory.
    """
    result = collapse_repeat_buffer_entries([])
    assert result == "(no prior ticks this window)", (
        f"Empty buffer must return the sentinel string; got {result!r}"
    )


# ---------------------------------------------------------------------------
# Collapse behaviour — repeat/no-op entries
# ---------------------------------------------------------------------------

def test_consecutive_repeat_entries_collapse_to_single_line() -> None:
    """Multiple consecutive repeat/no-op entries must collapse to one summary line.

    The collapsed line must reference the span (e.g. "tick(s)") so the
    strategist knows multiple ticks elapsed, not just one.

    This is the primary content assertion — at least two repeat entries must
    produce fewer output lines than entries.
    """
    buffer = [
        _entry("no_action", is_repeat=True),
        _entry("no_action", is_repeat=True),
        _entry("no_action", is_repeat=True),
    ]
    result = collapse_repeat_buffer_entries(buffer)

    # Three repeat entries must not produce three separate lines.
    # Count lines that are not the header.
    lines = [ln for ln in result.splitlines() if ln.strip() and not ln.startswith("Memory Buffer")]
    assert len(lines) == 1, (
        f"Three consecutive repeat entries must collapse to ONE summary line; "
        f"got {len(lines)} lines:\n{result}"
    )

    # The summary line must mention "no action" or "repeat" to be meaningful.
    assert "no action" in result.lower() or "repeat" in result.lower(), (
        "Collapsed summary must describe the run as no-action / repeat"
    )


def test_action_entries_preserved_verbatim() -> None:
    """Entries with executions_count > 0 must appear verbatim in the output.

    Action entries must NOT be swallowed into a repeat-run summary; losing a
    trade record from the prompt would be a silent-degradation bug.
    """
    buffer = [
        _entry("morning_sweep",     is_repeat=False, executions_count=2, summary="Bought AAPL"),
        _entry("afternoon_hold",    is_repeat=True,  executions_count=0),
        _entry("close_on_weakness", is_repeat=False, executions_count=1, summary="Sold MSFT on drop"),
    ]
    result = collapse_repeat_buffer_entries(buffer)

    # Both action tags must appear in the output.
    assert "morning_sweep" in result, (
        "Action entry 'morning_sweep' (executions=2) must be preserved verbatim"
    )
    assert "close_on_weakness" in result, (
        "Action entry 'close_on_weakness' (executions=1) must be preserved verbatim"
    )

    # The action entries' summaries must appear.
    assert "Bought AAPL" in result, "reasoning_summary of first action entry must be present"
    assert "Sold MSFT on drop" in result, "reasoning_summary of second action entry must be present"


def test_action_entries_preserve_chronological_order() -> None:
    """Action entries must appear in the same order as in the buffer (oldest first).

    Out-of-order action entries would mislead the strategist about the
    timeline of its own decisions.
    """
    buffer = [
        _entry("first_buy",  is_repeat=False, executions_count=1, summary="entry A"),
        _entry("no_action",  is_repeat=True,  executions_count=0),
        _entry("second_buy", is_repeat=False, executions_count=1, summary="entry B"),
    ]
    result = collapse_repeat_buffer_entries(buffer)

    idx_first  = result.index("first_buy")
    idx_second = result.index("second_buy")

    assert idx_first < idx_second, (
        "Action entries must appear in chronological (oldest-first) order; "
        f"'first_buy' index={idx_first}, 'second_buy' index={idx_second}"
    )


# ---------------------------------------------------------------------------
# Label accuracy
# ---------------------------------------------------------------------------

def test_header_reflects_actual_buffer_length() -> None:
    """The header must state the true number of ticks, not a hardcoded constant.

    The old prompt label said "last 8 ticks" regardless of the actual buffer
    length (which can reach 24 before eviction).  The new render derives the
    count from the buffer so the label is always accurate.
    """
    # Five entries — the label must cite 5, not 8 or 24.
    buffer = [_entry() for _ in range(5)]
    result = collapse_repeat_buffer_entries(buffer)

    # The header line must contain the digit "5".
    header_line = result.splitlines()[0]
    assert "5" in header_line, (
        f"Header must reflect actual buffer length (5); got: {header_line!r}"
    )

    # Belt-and-braces: the old hardcoded "8" must NOT be in the header.
    assert "last 8 ticks" not in header_line, (
        "Header must not repeat the old hardcoded 'last 8 ticks' label"
    )


def test_single_action_entry_produces_one_data_line() -> None:
    """A buffer with one action entry must produce exactly one data line.

    Regression guard: the helper must not produce duplicate lines when there
    is no collapsing to do.
    """
    buffer = [_entry("buy_aapl", executions_count=1, summary="high conviction buy")]
    result = collapse_repeat_buffer_entries(buffer)

    data_lines = [ln for ln in result.splitlines() if ln.strip() and not ln.startswith("Memory Buffer")]
    assert len(data_lines) == 1, (
        f"One action entry must produce exactly one data line; got {len(data_lines)}:\n{result}"
    )
    assert "buy_aapl" in result
