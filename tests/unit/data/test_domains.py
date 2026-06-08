"""Canonical DOMAINS frozenset lives in one module only."""
from data.domains import DOMAINS


def test_domains_contains_expected_eight_domains():
    """Eight domains — the surviving set after the 2026-05-26 provider cull
    (plan 08 removed earnings, analyst_consensus, short_interest, options)."""
    assert frozenset({
        "price_history", "company_ratios", "news", "social_sentiment",
        "insider_trades", "politician_trades", "notable_holders", "filings",
    }) == DOMAINS


def test_data_config_and_data_registry_use_the_same_object():
    """No accidental drift between the two consumers — both must read from the leaf."""
    from data.config import _DOMAINS as config_domains  # internal alias inside config
    from data.registry import DOMAINS as registry_domains
    assert config_domains is registry_domains is DOMAINS
