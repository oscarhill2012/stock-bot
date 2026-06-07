"""Validate that every (domain, name) pair in ``config/data.json`` resolves in
the provider registry.

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
