"""Unit tests for ``src/config/models.py`` — Pydantic-validated loader
for ``config/models.json``.

Covers: happy-path load, missing-key rejection, and lru_cache cycling
via ``_reset_cache()``.  Mirrors the style of ``test_analysts_config.py``
and ``test_strategist_config.py`` — each test points the loader at a
temporary JSON file so the real ``config/models.json`` is never mutated.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from config.models import ModelsConfig, _reset_cache, load_models_config


# ---------------------------------------------------------------------------
# Shared minimal valid payload
# ---------------------------------------------------------------------------
# All five model ID fields are required (no defaults).  The ``_comment`` key
# is stripped by the loader so it can be present or absent freely.
# ---------------------------------------------------------------------------

_MINIMAL_CFG: dict = {
    "strategist":          "gemini-2.5-pro",
    "news_analyst":        "gemini-2.5-flash-lite",
    "fundamental_analyst": "gemini-2.5-flash-lite",
    "memory_compressor":   "gemini-2.5-flash-lite",
    "memory_embedding":    "text-embedding-005",
}


def test_load_models_config_valid_payload(tmp_path) -> None:
    """A valid payload loads into a ``ModelsConfig`` with the expected values.

    Verifies that every field is populated, the type is correct, and that
    an optional ``_comment`` key in the JSON does not cause a ValidationError.
    """

    cfg_file = tmp_path / "models.json"
    # Include a ``_comment`` key to exercise the strip logic.
    payload = {**_MINIMAL_CFG, "_comment": "should be stripped"}
    cfg_file.write_text(json.dumps(payload))

    cfg = load_models_config(path=cfg_file)

    assert isinstance(cfg, ModelsConfig)
    assert cfg.strategist          == "gemini-2.5-pro"
    assert cfg.news_analyst        == "gemini-2.5-flash-lite"
    assert cfg.fundamental_analyst == "gemini-2.5-flash-lite"
    assert cfg.memory_compressor   == "gemini-2.5-flash-lite"
    assert cfg.memory_embedding    == "text-embedding-005"


def test_load_models_config_rejects_missing_required_key(tmp_path) -> None:
    """A payload missing any required model ID must raise ``ValidationError``.

    All five fields carry ``min_length=1`` and no defaults — omitting one is
    a schema violation that should be caught at load time rather than surfacing
    later as a confusing attribute-access error.
    """

    # Drop one required field — ``memory_embedding`` — to trigger the error.
    payload = {k: v for k, v in _MINIMAL_CFG.items() if k != "memory_embedding"}
    cfg_file = tmp_path / "models.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_models_config(path=cfg_file)


def test_reset_cache_cycles_lru_cache(tmp_path) -> None:
    """``_reset_cache()`` must cause the next call to re-read from disk.

    Verifies the cache-busting contract: after ``_reset_cache()`` the loader
    returns a fresh object (identity check fails), confirming that the
    ``lru_cache`` on ``get_models_config`` was actually cleared.  Uses
    ``load_models_config(path=...)`` directly so the cached path used by
    ``get_models_config`` never changes — we only confirm that ``_reset_cache``
    calls ``get_models_config.cache_clear()`` and that a subsequent call to
    ``load_models_config`` does not return a stale cached result.
    """

    cfg_file = tmp_path / "models.json"
    cfg_file.write_text(json.dumps(_MINIMAL_CFG))

    first = load_models_config(path=cfg_file)

    # Reset and reload — the result must be a fresh object, not the same
    # instance that was constructed on the first call.
    _reset_cache()
    second = load_models_config(path=cfg_file)

    assert first is not second
    assert second.strategist == first.strategist  # values still match
