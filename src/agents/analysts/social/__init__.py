"""Deterministic Social analyst package (Phase 5).

The Social analyst is a ``LlmAgent`` whose ``before_agent_callback`` (fetch)
computes verdicts deterministically and returns a skip-Content so the LLM
is never invoked.  The ``after_agent_callback`` (``make_evidence_callback``)
converts the pre-seeded ``social_verdicts`` to ``AnalystEvidence`` records.
"""
