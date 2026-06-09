"""Unit test for the consolidated _git_sha helper (A-084)."""
from __future__ import annotations

import pytest

from backtest.runner import _git_sha


def test_git_sha_length_variants():
    """`_git_sha()` returns the full SHA; `length=7` returns the short form.

    Runs against the real repo HEAD.  Skips gracefully if git is
    unavailable (helper returns the ``"unknown"`` sentinel)."""

    full  = _git_sha()
    short = _git_sha(length=7)

    if full == "unknown" or short == "unknown":
        pytest.skip("git not available in this environment — nothing to assert")

    assert len(full) == 40
    assert len(short) == 7
    assert full.startswith(short)
