"""Loader for ``config/strategist.json`` — character caps on strategist LLM fields.

The strategist produces free-text fields (``reasoning``, ``thesis``,
per-stance ``rationale``, ``catalyst``, ``close_reason``, ``trim_reason``, and
``PositionThesis`` rationale/notes).  Each is capped via ``pydantic.Field
(max_length=...)`` to keep prompts and persistence rows bounded.  The caps used
to live as magic numbers in the schemas themselves; centralising them here
makes them tunable without a code change.

A note on philosophy: **more is not always better.**  These caps are summary
budgets, not space for the LLM to pour its whole chain-of-thought into.  If we
ever feel the urge to keep raising them, the right move is usually a separate
retrieval layer (RAG over historical rationales), not bloating the on-tick
payload.  Treat the caps as a forcing function for concise reasoning.

A note on slack — the prompt vs. schema cap gap
-----------------------------------------------
LLMs do not count characters reliably.  They tokenise on subword boundaries,
not characters, so a "≤600 chars" instruction is interpreted as a fuzzy
*vibe* about length rather than a counted budget.  In live runs we observe
the strategist overshooting any stated cap by roughly 1–5%, occasionally up
to 10%.  Hard-truncating that overshoot mid-sentence loses meaning right
where the conclusion usually sits (LLM outputs tend to summarise at the
end), and a clip module adds non-trivial complexity for what is essentially
a counting bug.

So we adopt a deliberate two-tier convention:

- The values in ``decision_caps`` / ``stance_caps`` / ``position_thesis_caps``
  are the **prompt-facing caps** — the numbers the LLM is told in the
  instruction template (e.g. "reasoning ≤600 chars").  These are the
  user-facing targets.
- The schema's ``Field(max_length=...)`` is derived from the prompt cap by
  applying ``slack_percent`` headroom via :meth:`StrategistConfig.schema_cap`.
  With ``slack_percent = 10`` a prompt cap of 600 yields a schema cap of 660.

The lie is intentional and comments at the call sites flag it.  Effect: the
model is given the truthful target, the schema absorbs its natural overshoot,
and we never truncate.  If the model is systematically *more* than 10% over,
schema validation raises — that is the signal to either raise ``slack_percent``
or to actually build a soft-clip module.  Until then this is the simplest
mechanism that keeps the data clean without losing information.

The module-level singleton ``get_strategist_config()`` is the production entry
point; ``load_strategist_config(path=...)`` exists for tests that want to feed
a custom file.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from config.analysts import LlmCaps  # Shared shape — imported to avoid drift

# Project-root-relative default path. The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather than
# relative to this file.
_DEFAULT_PATH = Path("config/strategist.json")


class DecisionCaps(BaseModel):
    """Caps on the top-level ``StrategistDecision`` LLM output fields.

    Attributes
    ----------
    reasoning_max_chars:
        Max length of ``StrategistDecision.reasoning`` — the overall summary
        the LLM emits across all stances.  Raised from the original 300 after
        live runs showed Gemini routinely wanted more headroom.
    thesis_max_chars:
        Max length of ``StrategistDecision.thesis`` — the optional standing
        market thesis update.  When non-null, overwrites ``state['user:thesis']``;
        when null/omitted, the prior thesis is carried forward unchanged.
    """

    reasoning_max_chars: int = Field(ge=50, le=2000)
    thesis_max_chars:    int = Field(ge=50, le=2000)


class StanceCaps(BaseModel):
    """Caps on the per-ticker ``TickerStance`` free-text fields.

    Attributes
    ----------
    rationale_max_chars:
        Brief justification for the stance.  Kept short to force the LLM to
        pick its strongest reason rather than waffle.
    catalyst_max_chars:
        Optional near-term catalyst description.
    close_reason_max_chars:
        Why the position is being fully closed (``intent == "close"``).
        Note: Band 1 routes this through ``stance.reason``; the legacy
        ``close_reason`` field on ``TickerStance`` is preserved until Band 3.
    trim_reason_max_chars:
        Why the position is being reduced but not fully closed (``intent == "trim"``).
        Note: same as above — routes through ``stance.reason`` from Band 1.
    """

    rationale_max_chars:    int = Field(ge=50,  le=1000)
    catalyst_max_chars:     int = Field(ge=20,  le=500)
    close_reason_max_chars: int = Field(ge=20,  le=500)
    trim_reason_max_chars:  int = Field(ge=20,  le=500)


class PositionThesisCaps(BaseModel):
    """Caps on the ``PositionThesis`` fields persisted for held positions.

    Attributes
    ----------
    rationale_max_chars:
        Why we entered the position — written once at open and carried
        forward.  Longer than the per-tick stance rationale because it must
        survive across many ticks of context drift.
    catalyst_max_chars:
        Optional named catalyst for the position.
    last_review_note_max_chars:
        Short note appended each tick we review (but do not close) the
        position.
    """

    rationale_max_chars:        int = Field(ge=50,  le=2000)
    catalyst_max_chars:         int = Field(ge=20,  le=500)
    last_review_note_max_chars: int = Field(ge=20,  le=1000)


class StrategistConfig(BaseModel):
    """Top-level shape of ``config/strategist.json``.

    Attributes
    ----------
    slack_percent:
        Headroom percentage applied when deriving the schema-enforced cap
        from the prompt-facing cap (see the "two-tier convention" note in
        the module docstring).  ``10`` means schemas accept up to 110% of
        the value the LLM is told.  Bounded ``[0, 50]`` — values above 50%
        suggest the prompt cap itself is wrong rather than the slack.
    decision_caps:
        Caps on the LLM's outer decision payload.
    stance_caps:
        Caps on each per-ticker stance.
    position_thesis_caps:
        Caps on the persisted thesis for held positions.
    llm:
        Per-call runtime caps for the strategist LLM (timeout, output
        tokens, and per-class retry budgets).  Shares the same
        :class:`~config.analysts.LlmCaps` shape as the analyst agents but
        carries larger defaults (180 s, 8000 tokens) suited to the
        strategist's full-watchlist stance output.
    """

    slack_percent:        int = Field(ge=0, le=50, default=10)
    decision_caps:        DecisionCaps
    stance_caps:          StanceCaps
    position_thesis_caps: PositionThesisCaps
    llm:                  LlmCaps                                # Per-call runtime caps (timeout, tokens, retries)

    def schema_cap(self, prompt_cap: int) -> int:
        """Derive the schema-enforced ``max_length`` from a prompt-stated cap.

        Applies the ``slack_percent`` headroom and rounds up.  Uses integer
        math (``(prompt_cap * (100 + slack) + 99) // 100``) rather than
        ``ceil(prompt_cap * 1.1)`` to avoid floating-point surprises —
        ``600 * 1.1`` yields exactly ``660.0`` but ``200 * 1.1`` yields
        ``220.00000000000003`` due to binary representation, so the two
        prompt caps would round inconsistently.  Integer math gives the
        same answer for both: 200 → 220, 600 → 660.

        Parameters
        ----------
        prompt_cap:
            The cap value the model is told in the prompt template.

        Returns
        -------
        int
            ``ceil(prompt_cap * (100 + slack_percent) / 100)`` — the value
            that goes into ``Field(max_length=...)`` on the schema field.
        """
        return (prompt_cap * (100 + self.slack_percent) + 99) // 100


def load_strategist_config(*, path: Path | None = None) -> StrategistConfig:
    """Read and validate ``config/strategist.json``.

    Parameters
    ----------
    path:
        Override the default path.  Useful in tests that want to supply a
        temporary file without touching the source tree.

    Returns
    -------
    StrategistConfig
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
    return StrategistConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_strategist_config() -> StrategistConfig:
    """Production entry point — cached load of the default config path.

    The result is memoised via ``lru_cache`` so the JSON file is only read
    once per process.  A process restart is required after editing
    ``config/strategist.json`` to pick up changes — Pydantic ``Field
    (max_length=...)`` constraints are baked into the model classes at import
    time and cannot be hot-reloaded.

    Returns
    -------
    StrategistConfig
        Validated configuration singleton.
    """
    return load_strategist_config()
