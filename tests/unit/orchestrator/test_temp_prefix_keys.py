"""Guard test — invocation-scoped keys must carry the ``temp:`` prefix.

ADK's documented ``temp:`` prefix is invocation-scoped: keys with that
prefix do not survive across ticks.  A2.6 renames seven textbook
invocation-scoped keys to use the prefix so accidental cross-tick reads
fail loudly instead of returning stale data.
"""
from __future__ import annotations

from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC = _PROJECT_ROOT / "src"


_FORBIDDEN_UNPREFIXED = (
    # (relative path under src/, forbidden bare key).
    ("agents/strategist/prompts.py",            "{held_positions_view}"),
    ("agents/strategist/prompts.py",            "{ticker_evidence}"),
    ("agents/contract/evidence_writer.py",      "ticker_evidence_objects"),
    ("agents/analysts/technical/fetch.py",      '"technical_data"'),
    ("agents/analysts/social/fetch.py",         '"social_data"'),
    ("agents/analysts/fundamental/fetch.py",    '"fundamental_data"'),
    ("agents/analysts/news/fetch.py",           '"news_data"'),
)


def test_no_bare_invocation_keys_in_source() -> None:
    """Every invocation-scoped key in the modify-list must be prefixed."""
    failures: list[str] = []
    for rel_path, forbidden in _FORBIDDEN_UNPREFIXED:
        text = (_SRC / rel_path).read_text(encoding="utf-8")
        if forbidden in text and f"temp:{forbidden.strip(chr(34))}" not in text:
            failures.append(f"{rel_path}: bare {forbidden!r} found without temp: prefix")
    assert not failures, "Unprefixed invocation keys still present:\n  " + "\n  ".join(failures)
