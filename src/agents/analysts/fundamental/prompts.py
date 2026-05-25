"""Fundamental analyst prompt — Phase 9 (single-ticker per-branch, closed vocab + insider supplement).

The narrowed Fundamental LLM reads MD&A excerpts, risk-factor excerpts, and
Form 4 footnotes (prose) for ONE ticker per call.  It also receives a
structured block of insider numerics (10b5-1 ratio, cluster flags, role rank,
derivative counts) to anchor its prose reasoning in quant context.  It emits
closed-vocabulary tags only — no free text in ``key_factors``.

Runtime context is delivered via two ADK session-state keys that the
per-ticker ``FundamentalFetchAgent`` populates before this branch's analyst runs:

- ``fundamental_context`` — a single-ticker block containing that ticker's
  filings excerpts and insider activity (numerics + footnotes).
- ``ticker`` — the single ticker bound to this branch.

These appear as ``{fundamental_context}`` and ``{ticker}`` in the rendered
instruction string so ADK's ``inject_session_state`` substitutes them at
agent-run time.
"""
from __future__ import annotations

from agents.analysts.heuristics import FundamentalVocabulary
from config.analysts import get_analysts_config

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
# Vocabulary tokens (single-brace) are substituted at agent-construction time
# by ``build_fundamental_instruction``.  Runtime state tokens
# ``{fundamental_context}`` and ``{ticker}`` are left intact as single-brace
# so ADK's state injector fills them each tick.  Char-cap placeholders (e.g.
# ``{rationale_max}``) are substituted at build time from
# ``config/analysts.json`` so the value the LLM is told stays in sync with
# the prompt-facing cap.  The schema's ``Field(max_length=...)`` derives a
# *larger* value from the same prompt cap via ``schema_cap()`` — see the
# "two-tier convention" note in ``src/config/strategist.py``.
# ---------------------------------------------------------------------------

_TEMPLATE = """You are the Fundamental analyst.

You are focused on a SINGLE ticker for this call: {ticker}

Reason over the company's filings prose (MD&A excerpts, risk factors) AND
the INSIDER ACTIVITY block (numeric flows + footnote prose). You must produce
a structured verdict for that single ticker.

The data block for {ticker} contains:

  -- COMPANY FILINGS (PROSE) --
    MD&A and risk-factor excerpts from recent 10-K / 10-Q / 8-K filings.

  -- INSIDER ACTIVITY (30d, structured) --
    Net Form-4 dollars, buy/sell counts, cluster flags, planned-sale ratio
    (10b5-1), top filer role, derivative counts.

  -- INSIDER FOOTNOTES (≤5, prose) --
    Free-text footnotes attached to individual Form 4 rows.

Closed vocabulary (use these tags ONLY in key_factors):

  guidance:<value>            ∈ {guidance_options}
  tone:<value>                ∈ {tone_options}
  risk:<value>                ∈ {risk_tags}
                                 (optionally suffixed with _added | _removed | _intensified
                                  when comparing against the prior filing in the dump)
  insider:<value>             ∈ {insider_signals}
  going_concern:true          when going-concern language is present

OUTPUT CONTRACT
---------------
You MUST emit every field listed below.  ``is_no_data`` and ``report`` are
REQUIRED on every call — there is no shorter legal output.  Emit fields in
this exact order:

  ticker        string — MUST be exactly "{ticker}"
  lean          ∈ {{bullish, bearish, neutral}}
  magnitude     ∈ [0, 1]
  confidence    ∈ [0, 1]
  is_no_data    boolean — true ONLY if BOTH the filings-prose block AND the
                insider-activity block are empty for this ticker; false in
                every other case (including ambiguous data).
  key_factors   list of closed-vocabulary tags — at least 1, at most 8.
  report        object with summary + drivers (schema below).  REQUIRED on
                every emit, including when is_no_data=true (then summary is
                "no filings or insider data" and drivers describe the absence).

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

OUTPUT EXAMPLE (shape only — your content must reflect the actual filings + insider data)
-----------------------------------------------------------------------------------------
{{
  "ticker": "{ticker}",
  "lean": "bearish",
  "magnitude": 0.5,
  "confidence": 0.7,
  "is_no_data": false,
  "key_factors": ["insider:cluster_selling", "risk:macro_added", "tone:cautious"],
  "report": {{
    "summary": "Recent 10-Q adds a macro-risk paragraph and softens forward tone; insider activity shows a cluster of discretionary executive sales over the last 30 days.",
    "drivers": [
      {{ "name": "Insider cluster sells", "direction": "bear",    "weight": 0.5,
         "body": "Net Form-4 dollars -$18M with 6 discretionary sells from C-suite; planned-sale ratio 0.10 so the bulk is open-market." }},
      {{ "name": "Macro risk added",      "direction": "bear",    "weight": 0.3,
         "body": "New paragraph in Risk Factors flags FX + interest-rate exposure not present in the prior filing." }},
      {{ "name": "Stable guidance",       "direction": "neutral", "weight": 0.2,
         "body": "No change to revenue / EPS guidance; offsets but does not erase the negative tone shift." }}
    ]
  }}
}}

Decision guidance (anchors — reason from the evidence; this is not a
decision tree):

- Lean reflects the dominant signal across guidance, tone, risk-factor
  changes, and insider activity.  Use the full bullish / bearish range as
  the evidence supports.

- Routine 10b5-1 (planned) sales are pre-scheduled and disclosed in advance.
  They are NEUTRAL signal — NOT bearish.
- Discretionary open-market sales are bearish; clusters of them are
  strongly so.

- Absence of insider activity is neutral, not bearish — default to neutral
  with low confidence when there is nothing material to say.

- Going-concern language present → strongly bearish (overrides other signals).
- Conflicting inputs → neutral with low confidence.

Stop emitting if you are about to repeat a token or symbol three or more times in a row.  Return the verdict as-is and never emit filler tokens.

--- TICKER DATA FOR {ticker} ---
{fundamental_context}
"""


def build_fundamental_instruction(vocab: FundamentalVocabulary) -> str:
    """Render the Fundamental LLM instruction with the closed vocabulary baked in.

    Substitutes the four vocab placeholder tokens (``{guidance_options}``,
    ``{tone_options}``, ``{risk_tags}``, ``{insider_signals}``) using
    ``str.format``.  The two runtime state tokens — ``{fundamental_context}``
    and ``{ticker}`` — are left intact in the returned string; the per-ticker
    branch factory substitutes ``{ticker}`` at build time, and ADK's
    ``inject_session_state`` substitutes ``{fundamental_context}`` from
    ``state["fundamental_context"]`` at run time (the per-ticker fetch agent
    writes a single-ticker block into that key — see Phase 9 spec §1).

    Parameters
    ----------
    vocab:
        Validated ``FundamentalVocabulary`` instance holding the four closed-
        vocabulary lists.

    Returns
    -------
    str
        The rendered instruction string.  Contains exactly two remaining
        single-brace tokens: ``{fundamental_context}`` and ``{ticker}``.
    """
    # Prompt-facing caps — what we tell the LLM.  ``schema_cap()`` no longer
    # applies on the LLM emit-schema (``LlmTickerVerdict`` / ``AnalystReport``)
    # because the ``max_length`` constraints were removed there to defuse
    # Vertex's pad-toward-cap pathology; we now state the bound in prose
    # only and trust the model to honour it.
    out_caps = get_analysts_config().output_caps

    return _TEMPLATE.format(
        guidance_options=" | ".join(vocab.guidance),
        tone_options     =" | ".join(vocab.tone),
        risk_tags        =" | ".join(vocab.risks),
        insider_signals  =" | ".join(vocab.insider_signals),
        # Prose-only character bounds for the report block.  The schema no
        # longer enforces them — the wording in the prompt is the bound.
        summary_max      = out_caps.report_summary_max_chars,
        driver_name_max  = out_caps.report_driver_name_max_chars,
        driver_body_max  = out_caps.report_driver_body_max_chars,
        # Protect the two runtime placeholders from str.format substitution
        # by passing them back as themselves.
        fundamental_context="{fundamental_context}",
        ticker             ="{ticker}",
    )
