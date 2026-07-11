"""News prompt requires ``report`` on every emit.

History: the prompt previously said ``omit only when is_no_data=true`` and
later ``REQUIRED whenever is_no_data=false``; the LLM violated both forms
(30.7 % on baseline-2025-09; the audit on post-mem-test-5 showed the same
pattern persisting because the LLM was treating optionality on the JSON
schema side as "ok to omit").  The 2026-05-25 schema split closes the
loophole at three layers — ``LlmTickerVerdict`` makes ``report`` required
(no Optional, no default), the prompt instructs the LLM that ``report`` is
``REQUIRED on every call`` (even when ``is_no_data=true`` — the no-data
case carries a one-line "no data" summary), and ``extra="forbid"`` fails
loudly on drift.  These tests pin the prompt-side guarantee.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from agents.analysts.news.prompts import build_news_instruction


def _vocab() -> NewsVocabulary:
    return NewsVocabulary(
        catalysts=["earnings", "guidance", "m_and_a", "none"],
        novelty=["high", "medium", "low"],
        direction=["positive", "negative", "mixed", "none"],
    )


def test_report_required_wording_present() -> None:
    """The unconditional-required wording must appear in the rendered prompt."""

    rendered = build_news_instruction(_vocab())

    # ``is_no_data`` and ``report`` are both required on every emit — the
    # prompt names them explicitly in the OUTPUT CONTRACT block.
    assert "REQUIRED on every call" in rendered

    # The contract block must also state that ``report`` is emitted in the
    # is_no_data=true branch (with a "no data" summary) so the LLM cannot
    # treat ``is_no_data=true`` as a licence to omit the report.
    assert "including when is_no_data=true" in rendered


def test_legacy_conditional_wording_absent() -> None:
    """The previous softer wordings must not coexist with the new hard rule.

    Both legacy phrasings are checked — the original (``omit only when
    is_no_data=true``) and its intermediate strengthening (``REQUIRED
    whenever is_no_data=false``) — because either one would leave room for
    the LLM to omit ``report`` in the no-data branch and re-open the
    failure mode the schema split was designed to close.

    A third regression closed here: a later rewrite paraphrased the legacy
    wording (``is_no_data=false`` became ``is_no_data is false``) and slipped
    past a substring-pinned test. Pinning exact substrings is fragile against
    paraphrase, so this test also asserts the *invariant* directly — no
    phrasing may instruct a null report on the no-data branch, and the
    no-data branch must still positively emit a report.
    """

    rendered = build_news_instruction(_vocab())
    assert "omit only when is_no_data=true"    not in rendered
    assert "REQUIRED whenever is_no_data=false" not in rendered

    # Paraphrase-proof guard: no phrasing anywhere may instruct a null
    # report on the no-data branch, however it is worded.
    assert "must then be null" not in rendered
    assert "report must be null" not in rendered.lower()

    # Positive guard: the no-data branch must still emit a report — a
    # one-line "no news in window" summary — rather than nulling it out.
    assert "no news in window" in rendered

    # The two reasoning axes that make the 2-driver minimum meaningful must
    # both be named in the OUTPUT CONTRACT prose.
    assert "DRIFT-WINDOW POSITION" in rendered
