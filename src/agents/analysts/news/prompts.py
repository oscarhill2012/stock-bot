"""News analyst prompt — Phase 9 (single-ticker per-branch, closed-vocab mandate).

The narrowed News LLM reads headlines and article summaries for ONE ticker
per call.  Polarity statistics (positive_score, negative_score,
mention_count) that previously lived in the prompt are removed; those
numeric features flow through the extractor channel instead.

Runtime context is delivered via two ADK session-state keys that the
per-ticker ``NewsFetchAgent`` populates before this branch's analyst runs:

- ``news_context`` — a single-ticker block containing that ticker's
  headline list and article summaries.
- ``ticker`` — the single ticker bound to this branch.

These appear as ``{news_context}`` and ``{ticker}`` in the rendered
instruction string so ADK's ``inject_session_state`` substitutes them at
agent-run time.
"""
from __future__ import annotations

from agents.analysts.heuristics import NewsVocabulary
from config.analysts import get_analysts_config

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
# Vocabulary tokens (single-brace) are substituted at agent-construction time
# by ``build_news_instruction``.  Runtime state tokens ``{news_context}`` and
# ``{ticker}`` are left intact as single-brace so ADK's state injector fills
# them each tick.  Char-cap placeholders (e.g. ``{rationale_max}``) are
# substituted at build time from ``config/analysts.json`` so the value the
# LLM is told stays in sync with the prompt-facing cap.  The schema's
# ``Field(max_length=...)`` derives a *larger* value from the same prompt
# cap via ``schema_cap()`` — see the "two-tier convention" note in
# ``src/config/strategist.py``.
# ---------------------------------------------------------------------------

_TEMPLATE = """You are the News analyst.

You are focused on a SINGLE ticker for this call: {ticker}

Read the supplied headlines and article summaries for that ticker.
Output ONE JSON object — a single verdict — using ONLY the closed
vocabulary below.

Closed vocabulary (use these tags ONLY in key_factors):

  catalyst:<type>     ∈ {catalyst_options}
  novelty:<level>     ∈ {novelty_options}
  direction:<value>   ∈ {direction_options}
  material:<bool>     when material to a long-only fund

OUTPUT CONTRACT
---------------
You MUST emit every field listed below.  ``is_no_data`` and ``report`` are
REQUIRED on every call — there is no shorter legal output.  Emit fields in
this exact order:

  ticker        string — MUST be exactly "{ticker}"
  lean          ∈ {{bullish, bearish, neutral}}
  magnitude     ∈ [0, 1]
  confidence    ∈ [0, 1]
  is_no_data    boolean — true ONLY if the headlines block is empty for this
                ticker; false in every other case (including ambiguous data).
  key_factors   list of closed-vocabulary tags — at least 1, at most 8.
  report        object with summary + drivers (schema below).  REQUIRED on
                every emit, including when is_no_data=true (then summary is
                "no news in window" and drivers describe the absence).

Report schema:
  summary  string — connective tissue covering the gestalt this tick. Argue
           your lean.  As brief as you like — one short paragraph is fine;
           there is NO minimum length beyond one sentence.  Hard upper limit
           of {summary_max} characters; do not pad.
  drivers  list of 2-4 entries.  Each driver:
    name       string — short label for the driver, ≤{driver_name_max} chars.
               Do not pad.
    direction  ∈ {{bull, bear, neutral}}
    weight     ∈ [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised.
    body       string — prose explanation. As brief as you like; hard upper
               limit of {driver_body_max} chars; do not pad. Do NOT cite
               source URLs; synthesise.

The report is your reasoning; the verdict is your conclusion. They must be
consistent — the lean and direction-weighted driver mix should agree.

OUTPUT EXAMPLE (shape only — your content must reflect the actual headlines)
---------------------------------------------------------------------------
{{
  "ticker": "{ticker}",
  "lean": "bullish",
  "magnitude": 0.6,
  "confidence": 0.7,
  "is_no_data": false,
  "key_factors": ["catalyst:earnings_beat", "novelty:high", "direction:positive", "material:true"],
  "report": {{
    "summary": "Q3 print beat consensus on revenue and EPS, with management raising full-year guidance on AI tailwinds.",
    "drivers": [
      {{ "name": "Earnings beat",     "direction": "bull",    "weight": 0.6,
         "body": "Revenue +8% YoY, EPS $1.42 vs $1.28 consensus; both segments outgrew the market." }},
      {{ "name": "Guidance raise",    "direction": "bull",    "weight": 0.3,
         "body": "FY revenue band lifted ~3% at the midpoint citing data-centre demand." }},
      {{ "name": "Macro uncertainty", "direction": "neutral", "weight": 0.1,
         "body": "Management flagged FX headwinds and consumer softness as offsetting risk factors." }}
    ]
  }}
}}

Decision rule:
- Lean ← direction: positive → bullish; negative → bearish; mixed/none → neutral.
- Magnitude ← novelty × material weight: high novelty + material → higher magnitude.
- Confidence scales with headline count; fewer than 3 articles caps confidence low.
- Conflicting direction signals across articles → mixed → neutral with low confidence.
- Bearish is appropriate for missed guidance, downgrade, supplier loss,
  executive departure, regulatory action, or adverse legal outcome —
  do NOT default to neutral when evidence is materially negative.

Stop emitting if you are about to repeat a token or symbol three or more times in a row.
Return the verdict as-is and never emit filler tokens.

--- HEADLINES & SUMMARIES FOR {ticker} ---
{news_context}
"""


def build_news_instruction(vocab: NewsVocabulary) -> str:
    """Render the News LLM instruction with the closed vocabulary baked in.

    Substitutes the three vocab placeholder tokens (``{catalyst_options}``,
    ``{novelty_options}``, ``{direction_options}``) using ``str.format``.
    The two runtime state tokens — ``{news_context}`` and ``{ticker}`` —
    are left intact in the returned string; the per-ticker branch factory
    substitutes ``{ticker}`` at build time, and ADK's
    ``inject_session_state`` substitutes ``{news_context}`` from
    ``state["news_context"]`` at run time (the per-ticker fetch agent
    writes a single-ticker block into that key — see Phase 9 spec §1).

    Parameters
    ----------
    vocab:
        Validated ``NewsVocabulary`` instance holding the three closed-
        vocabulary lists.

    Returns
    -------
    str
        The rendered instruction string.  Contains exactly two remaining
        single-brace tokens: ``{news_context}`` and ``{ticker}``.
    """
    # Prompt-facing caps — what we tell the LLM.  ``schema_cap()`` no longer
    # applies on the LLM emit-schema (``LlmTickerVerdict`` / ``AnalystReport``)
    # because the ``max_length`` constraints were removed there to defuse
    # Vertex's pad-toward-cap pathology; we now state the bound in prose
    # only and trust the model to honour it.  The deterministic-extractor
    # path still uses ``schema_cap()`` for its own caps.
    out_caps = get_analysts_config().output_caps

    return _TEMPLATE.format(
        catalyst_options ="{" + " | ".join(vocab.catalysts) + "}",
        novelty_options  ="{" + " | ".join(vocab.novelty)   + "}",
        direction_options="{" + " | ".join(vocab.direction)  + "}",
        # Prose-only character bounds for the report block.  The schema no
        # longer enforces them — the wording in the prompt is the bound.
        summary_max      = out_caps.report_summary_max_chars,
        driver_name_max  = out_caps.report_driver_name_max_chars,
        driver_body_max  = out_caps.report_driver_body_max_chars,
        # Protect the two runtime placeholders from str.format substitution
        # by passing them back as themselves.
        news_context="{news_context}",
        ticker      ="{ticker}",
    )
