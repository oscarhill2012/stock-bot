"""Loader for ``config/analysts.json`` — truncation caps + cache settings.

A Pydantic-validated wrapper around the JSON file at the project root. The
module-level singleton ``get_analysts_config()`` is the production entry
point; ``load_analysts_config(path=...)`` exists for tests that want to feed
a custom file.

The config is split into two flavours of cap:

- **Input caps** (``news.*``, ``fundamental.*``) — bound the data fed *into*
  the analyst LLMs.  Deterministic, governed by us, no slack needed.
- **Output caps** (``output_caps.*``) — bound the free-text fields the LLMs
  emit (``Verdict.rationale``, ``AnalystReport.summary``, ``ReportDriver``
  fields).  Subject to the same character-counting blind spot as the
  strategist, so the prompt-facing value sits below the schema-enforced
  value via the ``slack_percent`` knob.  See the "two-tier convention" note
  in ``src/config/strategist.py`` for the full rationale — this module
  duplicates the convention because each LLM tier should be able to tune
  its slack independently of the others.
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


class OutputCaps(BaseModel):
    """Prompt-facing char caps on analyst LLM free-text output fields.

    The values here are what the LLM is told in the prompt (e.g. "≤160
    chars").  The Pydantic schemas in ``src/contract/evidence.py`` derive
    their ``Field(max_length=...)`` from these via
    :meth:`AnalystsConfig.schema_cap`, which applies ``slack_percent``
    headroom.  Do **not** "fix" the apparent mismatch between the values
    here and the literal numbers Pydantic enforces — the gap is
    intentional (see the module docstring).

    Attributes
    ----------
    verdict_rationale_max_chars:
        Cap on ``AnalystVerdict.rationale`` — one-line summary of the
        dominant catalyst/finding.  Tightest cap in the system; ripest for
        slack to bite.
    report_summary_max_chars:
        Cap on ``AnalystReport.summary`` — the 3–5 sentence gestalt that
        argues the lean.
    report_driver_name_max_chars:
        Cap on ``ReportDriver.name`` — short label (4–6 words).
    report_driver_body_max_chars:
        Cap on ``ReportDriver.body`` — 2–3 sentence explanation per driver.
    """

    verdict_rationale_max_chars:    int = Field(ge=50,  le=1000)
    report_summary_max_chars:       int = Field(ge=200, le=8000)
    report_driver_name_max_chars:   int = Field(ge=20,  le=200)
    report_driver_body_max_chars:   int = Field(ge=100, le=4000)


class AnalystsConfig(BaseModel):
    """Top-level shape of ``config/analysts.json``.

    Attributes
    ----------
    slack_percent:
        Headroom percentage applied when deriving the schema-enforced
        ``max_length`` on each ``output_caps`` value from its prompt-facing
        value.  ``10`` means the schema accepts up to 110% of the value the
        LLM is told in the prompt.  Independent of the strategist's
        ``slack_percent`` so each LLM tier can be tuned separately.
        Bounded ``[0, 50]``.
    news:
        Input-side truncation caps for the News analyst.
    fundamental:
        Input-side truncation caps for the Fundamental analyst.
    output_caps:
        Prompt-facing char caps on analyst LLM output fields.
    cache:
        Report-cache toggle + storage directory.
    """

    slack_percent: int = Field(ge=0, le=50, default=10)
    news:          NewsCaps
    fundamental:   FundamentalCaps
    output_caps:   OutputCaps
    cache:         CacheSettings

    def schema_cap(self, prompt_cap: int) -> int:
        """Derive the schema-enforced ``max_length`` from a prompt-stated cap.

        Mirror of :meth:`config.strategist.StrategistConfig.schema_cap`.
        Uses integer math — ``(prompt_cap * (100 + slack) + 99) // 100`` —
        rather than ``ceil(prompt_cap * 1.1)`` to dodge floating-point
        rounding inconsistencies (``600 * 1.1`` is exactly ``660.0`` but
        ``200 * 1.1`` is ``220.00000000000003``, which would round
        differently).  Integer math gives 200→220 and 600→660 alike.

        Parameters
        ----------
        prompt_cap:
            The cap value the LLM is told in the prompt template.

        Returns
        -------
        int
            The schema-enforced ``max_length``.
        """
        return (prompt_cap * (100 + self.slack_percent) + 99) // 100


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
