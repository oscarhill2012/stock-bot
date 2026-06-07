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


def test_data_json_domains_exactly_match_registry_domains() -> None:
    """config/data.json must cover exactly the registry's DOMAINS — no more, no less.

    Guards against a domain being silently dropped from (or added to)
    ``config/data.json`` without the registry agreeing.  This is the
    audit-cull regression guard: a provider cull that removes a domain from
    the registry but leaves a stale entry in the config (or vice-versa)
    will fail here immediately rather than silently degrading at runtime.
    """
    from data.registry import DOMAINS

    cfg = json.loads(Path("config/data.json").read_text())

    config_domains = set(cfg["providers"].keys())

    assert config_domains == set(DOMAINS), (
        f"domain mismatch between config/data.json and registry DOMAINS.\n"
        f"  In config only: {config_domains - set(DOMAINS)}\n"
        f"  In registry only: {set(DOMAINS) - config_domains}"
    )
