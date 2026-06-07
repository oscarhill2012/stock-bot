"""Validate that every (domain, name) pair in ``config/data.json`` resolves in
the provider registry, and that all four Phase 3 domains are present.

Phase 6 of providers-and-silent-gaps-v1.
"""
from __future__ import annotations

import json
from pathlib import Path


def test_config_data_json_provider_names_resolve_in_registry() -> None:
    """Every provider listed in config/data.json must have a registered fetch function.

    Imports ``data.providers`` first to trigger all ``@register`` calls, then
    checks that each (domain, name) pair in the config is present in the live
    registry.  This catches mismatches between config edits and missing provider
    modules before they surface at runtime.
    """
    import data.providers  # noqa: F401 — force all @register decorators to fire

    from data.registry import DOMAINS, _REGISTRY

    cfg = json.loads(Path("config/data.json").read_text())

    for domain, name in cfg["providers"].items():
        assert domain in DOMAINS, f"unknown domain in config: {domain}"
        assert (domain, name) in _REGISTRY, (
            f"missing registry entry: ({domain!r}, {name!r})"
        )


def test_config_data_json_lists_phase3_domains() -> None:
    """Surviving Phase 3 domains must be present in config/data.json providers block.

    Ensures the config keeps pace with registry expansion — a domain can be
    registered but silently absent from config, which would leave the live
    pipeline fetching nothing for that domain.
    """
    cfg = json.loads(Path("config/data.json").read_text())

    for domain in ("earnings", "analyst_consensus"):
        assert domain in cfg["providers"], (
            f"Phase 3 domain {domain!r} missing from config/data.json providers block"
        )
