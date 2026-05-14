"""Loader for ``config/analysts.json`` — truncation caps + cache settings.

A Pydantic-validated wrapper around the JSON file at the project root. The
module-level singleton ``get_analysts_config()`` is the production entry
point; ``load_analysts_config(path=...)`` exists for tests that want to feed
a custom file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path. The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather than
# to this file.
_DEFAULT_PATH = Path("config/analysts.json")


class NewsCaps(BaseModel):
    """Truncation caps for the News analyst's LLM context."""

    max_articles_per_ticker: int = Field(ge=1, le=200)
    max_summary_chars:       int = Field(ge=1, le=10_000)


class FundamentalCaps(BaseModel):
    """Truncation caps for the Fundamental analyst's LLM context."""

    max_filing_mda_chars:       int = Field(ge=1, le=20_000)
    max_filing_risk_chars:      int = Field(ge=1, le=20_000)
    max_insider_footnotes:      int = Field(ge=0, le=50)
    max_insider_footnote_chars: int = Field(ge=1, le=5_000)


class CacheSettings(BaseModel):
    """Report-cache toggle + on-disk storage directory (gitignored)."""

    enabled:   bool
    directory: str


class AnalystsConfig(BaseModel):
    """Top-level shape of ``config/analysts.json``."""

    news:        NewsCaps
    fundamental: FundamentalCaps
    cache:       CacheSettings


def load_analysts_config(*, path: Path | None = None) -> AnalystsConfig:
    """Read and validate ``config/analysts.json``.

    Parameters
    ----------
    path:
        Override the default path. Useful in tests that want to supply a
        temporary file without touching the source tree.

    Returns
    -------
    AnalystsConfig
        Validated configuration object.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist at the resolved path.
    json.JSONDecodeError
        If the file content is not valid JSON.
    pydantic.ValidationError
        If the parsed payload fails schema validation.
    """
    p = path or _DEFAULT_PATH
    payload = json.loads(p.read_text(encoding="utf-8"))
    return AnalystsConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_analysts_config() -> AnalystsConfig:
    """Production entry point — cached load of the default config path.

    The result is memoised via ``lru_cache`` so the JSON file is only read
    once per process. A process restart is required after editing
    ``config/analysts.json`` to pick up changes.

    Returns
    -------
    AnalystsConfig
        Validated configuration singleton.
    """
    return load_analysts_config()
