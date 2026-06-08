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

from config._slack import apply_slack

# Project-root-relative default path. The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather than
# to this file.
_DEFAULT_PATH = Path("config/analysts.json")


class LlmCaps(BaseModel):
    """Per-LLM-agent runtime caps used by the retry wrapper.

    Each LLM-calling agent (each analyst, the strategist) carries its own
    instance of this block in its config file.  The wrapper reads
    ``timeout_seconds`` to bound each call's wall-clock time, the LlmAgent
    receives ``max_output_tokens`` via ``GenerateContentConfig`` to bound
    output length, and the wrapper composes per-class retry budgets from
    ``timeout_retries`` and ``schema_retries``.

    The project-wide HTTP 429 policy is **not** here — it lives in
    ``config/retry_429.json`` because it is identical across agents.

    Attributes
    ----------
    timeout_seconds:
        Per-call wall-clock timeout in seconds.  Enforced via
        ``asyncio.wait_for(...)`` inside ``RetryingAgentWrapper``.  Range
        ``(0, 600]``.
    max_output_tokens:
        Cap on the model's generated output tokens.  Set on every call
        (not just retries) so output loops cannot wedge the tick in the
        first place.  Range ``[256, 32768]``.
    timeout_retries:
        Total attempts the wrapper makes when wall-clock timeouts fire.
        ``3`` means one initial try plus up to two retries.  Range
        ``[1, 10]``.
    schema_retries:
        Total attempts the wrapper makes when ``pydantic.ValidationError``
        fires (output_schema parse failed).  Same shape as
        ``timeout_retries``.
    """

    timeout_seconds:   float = Field(gt=0.0, le=600.0)
    max_output_tokens: int   = Field(ge=256, le=32_768)
    timeout_retries:   int   = Field(ge=1, le=10)
    schema_retries:    int   = Field(ge=1, le=10)


class NewsCaps(BaseModel):
    """Truncation caps for the News analyst's LLM context."""

    max_articles_per_ticker: int     = Field(ge=1, le=200)
    max_summary_chars:       int     = Field(ge=1, le=10_000)
    llm:                     LlmCaps                           # NEW — per-call runtime caps


class FundamentalCaps(BaseModel):
    """Truncation caps for the Fundamental analyst's LLM context."""

    max_filing_mda_chars:       int     = Field(ge=1, le=20_000)
    max_filing_risk_chars:      int     = Field(ge=1, le=20_000)
    max_insider_footnotes:      int     = Field(ge=0, le=50)
    max_insider_footnote_chars: int     = Field(ge=1, le=5_000)
    llm:                        LlmCaps                        # NEW — per-call runtime caps


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

    # Prompt-facing headroom — derived budget shown to the LLM is
    # ``verdict_rationale_max_chars - verdict_rationale_prompt_headroom_chars``
    # (with safety clamps).  Keeps the prompt tighter than the schema cap so
    # the LLM's natural 1–5 % overshoot does not trip ``string_too_long``.
    verdict_rationale_prompt_headroom_chars: int = Field(ge=-100, le=1000, default=50)

    report_summary_max_chars:       int = Field(ge=200, le=8000)
    report_driver_name_max_chars:   int = Field(ge=20,  le=200)
    report_driver_body_max_chars:   int = Field(ge=100, le=4000)

    @property
    def verdict_rationale_prompt_budget(self) -> int:
        """Prompt-facing rationale budget — the value the LLM is told.

        Derived from the schema-facing cap minus the configured headroom so
        raising or lowering ``verdict_rationale_max_chars`` automatically
        re-tunes what the LLM is asked to produce.  The result is clamped on
        both sides:
          * lower bound 40 — a meaningless or negative budget can never
            reach the prompt (catches headroom > cap misconfigurations);
          * upper bound ``verdict_rationale_max_chars`` — the prompt budget
            can never exceed the schema cap, defeating the purpose (catches
            negative-headroom misconfigurations).
        """

        budget = (
            self.verdict_rationale_max_chars
            - self.verdict_rationale_prompt_headroom_chars
        )
        return max(40, min(self.verdict_rationale_max_chars, budget))


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

        Thin delegation to :func:`config._slack.apply_slack` — see that
        function for the integer-math rationale.

        Parameters
        ----------
        prompt_cap:
            The cap value the LLM is told in the prompt template.

        Returns
        -------
        int
            The schema-enforced ``max_length``.
        """
        return apply_slack(prompt_cap, self.slack_percent)


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
