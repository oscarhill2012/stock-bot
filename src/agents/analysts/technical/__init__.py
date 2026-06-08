"""Deterministic Technical analyst package (Phase 5).

Mirrors the Social analyst package — see ``agents.analysts.social`` for the
LlmAgent-vs-BaseAgent rationale.  Production callers construct via the
``_build_technical_analyst`` factory in ``agents.analysts.technical.agent``;
there is intentionally no module-level singleton (singletons executed file
I/O at import time and made misconfiguration silent).
"""
