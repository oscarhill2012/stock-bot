"""Shared fixtures for the integration test suite.

Pytest auto-discovers this file and makes the fixtures available to every
module in ``tests/integration/`` without explicit imports.
"""
from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Config redirect fixture — points AnalystsConfig at a tmp_path directory
# ---------------------------------------------------------------------------

@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    """Point AnalystsConfig at a tmp_path cache directory.

    Writes a minimal ``analysts.json`` pointing at ``tmp_path/cache``, patches
    the module-level ``_DEFAULT_PATH`` in ``config.analysts``, and clears the
    ``lru_cache`` so the fresh config is loaded.  Clears the cache again on
    teardown so subsequent tests start clean.

    Parameters
    ----------
    tmp_path:
        pytest-provided temporary directory (unique per test).
    monkeypatch:
        pytest monkeypatch fixture for safe attribute patching.

    Yields
    ------
    Path
        Absolute path to the tmp cache root (``tmp_path / "cache"``).
    """
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": 20, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "output_caps": {
            "verdict_rationale_max_chars":   160,
            "report_summary_max_chars":     2000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 1000,
        },
        "cache": {"enabled": True, "directory": str(tmp_path / "cache")},
    }))

    from config import analysts as cfg_mod
    cfg_mod.get_analysts_config.cache_clear()
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", cfg_file)

    yield tmp_path / "cache"

    # Teardown — clear so later tests load their own config.
    cfg_mod.get_analysts_config.cache_clear()


# ---------------------------------------------------------------------------
# Minimal callback-context stub
# ---------------------------------------------------------------------------

class _Ctx:
    """Minimal callback-context stub that exposes a mutable ``state`` dict.

    Used by cache-callback integration tests as a stand-in for ADK's
    ``CallbackContext``.  Only ``state`` access is needed; no other ADK
    internals are exercised by the cache layer.
    """

    def __init__(self, state: dict):
        """Initialise with an arbitrary state dict.

        Parameters
        ----------
        state:
            Mutable dict that the cache callbacks read from and write to.
        """
        self.state = state


def make_ctx(state: dict) -> _Ctx:
    """Factory function that returns a ``_Ctx`` stub pre-populated with *state*.

    Prefer this over constructing ``_Ctx`` directly in test modules so that
    the stub's internals can be changed in one place if needed.

    Parameters
    ----------
    state:
        Initial state dict for the stub context.

    Returns
    -------
    _Ctx
        A new callback-context stub.
    """
    return _Ctx(state)
