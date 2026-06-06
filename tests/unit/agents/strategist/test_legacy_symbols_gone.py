"""Guard that the retired legacy strategist surface is genuinely unreachable.

Plan 07 retires three symbols from the live tree:

  - ``agents.strategist.agent._strategist_validation_callback``
  - ``agents.strategist.enricher.build_strategist_enricher``
  - ``agents.strategist.evidence_view`` (entire module)

Any future re-introduction (whether as a fresh definition or as a
re-export) would silently revive a parallel code path next to the
sequenced ``StrategistEnricher``.  This test fails loudly the moment any
of those names becomes importable again — no silent revival.

We assert via ``importlib`` so a typo in the symbol name surfaces here
rather than as a false-green PASS.
"""
from __future__ import annotations

import importlib

import pytest


def test_strategist_validation_callback_is_gone() -> None:
    """The legacy after_agent_callback shim must not exist on the agent module."""

    agent_module = importlib.import_module("agents.strategist.agent")
    assert not hasattr(agent_module, "_strategist_validation_callback"), (
        "agents.strategist.agent._strategist_validation_callback was retired "
        "in Plan 07 — production uses the sequenced StrategistEnricher.  "
        "Reintroducing this symbol revives a parallel enrichment path."
    )


def test_build_strategist_enricher_factory_is_gone() -> None:
    """The single-caller factory must not exist on the enricher module."""

    enricher_module = importlib.import_module("agents.strategist.enricher")
    assert not hasattr(enricher_module, "build_strategist_enricher"), (
        "build_strategist_enricher was a single-caller factory with no DI "
        "surface — retired in Plan 07.  Construct StrategistEnricher() "
        "directly."
    )


def test_evidence_view_module_is_gone() -> None:
    """The dead evidence_view renderer module must not be importable."""

    # agents.strategist.evidence_view was retired in Plan 07 alongside the
    # legacy callback.  Re-adding it revives the dual-surface render path.
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("agents.strategist.evidence_view")
