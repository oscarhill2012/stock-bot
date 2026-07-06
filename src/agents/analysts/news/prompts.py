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
                List DISTINCT tags only — never repeat a tag; if you cannot
                find a second distinct driver, you do not have one.
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

SHAPE EXAMPLE (placeholders only — fill from the actual headlines):
{{
  "ticker": "{ticker}",
  "lean": "<bullish|bearish|neutral>",
  "magnitude": <0.0-1.0>,
  "confidence": <0.0-1.0>,
  "is_no_data": false,
  "key_factors": ["<closed-vocab tag>", "..."],
  "report": {{
    "summary": "<one short paragraph arguing the lean from the headlines>",
    "drivers": [
      {{ "name": "<short label>", "direction": "<bull|bear|neutral>",
         "weight": <0.0-1.0>, "body": "<prose; cite the evidence>" }},
      {{ "name": "<short label>", "direction": "<bull|bear|neutral>",
         "weight": <0.0-1.0>, "body": "<prose; cite the evidence>" }}
    ]
  }}
}}

Decision rule:
- Lean is driven by NEW, incremental, company-specific information — not by
  overall sentiment.  Positive coverage of a stable large-cap is the DEFAULT
  state and is NOT a reason to be bullish.
    bullish ← at least one genuinely-new positive company-specific catalyst
              (0–2 days old, not a restatement of older news), with no
              comparably fresh negative catalyst.
    bearish ← at least one genuinely-new material negative signal.
    neutral ← everything else: positive-but-already-priced-in, mixed signals,
              opinion or price commentary only, or nothing fresh.
              Do NOT default to bullish.
- Magnitude ← expected size of the 1–3 session move if the lean is right
  (novelty × materiality).
- Confidence ← how sure you are the news is real, unpriced, AND material.
  Confidence is NOT driven by article COUNT — 200 "great stock" pieces are
  weaker than one report of a missed guide.  Fewer than ~3 genuinely
  company-specific articles caps confidence low.
- Conflicting direction signals across articles → mixed → neutral with low confidence.
- Bearish is appropriate for missed guidance, downgrade, supplier loss,
  executive departure, regulatory action, or adverse legal outcome —
  do NOT default to neutral when evidence is materially negative.

Recency and already-priced-in discount:
- The context block opens with an ``As of:`` anchor date.  Each headline
  shows how many days old it is (e.g. ``3d ago``).  Use this information
  actively — do not treat all articles as equally fresh.
- Weight recent articles (0–1 days old) more heavily; treat older articles
  as progressively less actionable because the market has had more time to
  absorb and price in the information.
- Widely-reported, multi-day-old stories (e.g. an earnings miss that broke
  three days ago and has been discussed everywhere) are likely already
  reflected in the current price.  Lower your magnitude and confidence
  accordingly, and lean neutral unless you see a clear incremental
  development that the market has *not* yet had time to react to.
- If the freshest article is several days old, lean toward neutral with low
  confidence rather than taking a strong directional position — stale news
  is weak signal at the 1-day horizon.
- If a published age is marked ``age unknown``, treat that article
  conservatively: do not let it anchor your confidence upward.

Source and signal quality:
- Weight: company disclosures, wire-service reporting of FACTS (earnings,
  contracts, approvals), and sell-side rating or price-target CHANGES that
  move outside the prior consensus range.
- Treat as NOISE (never a driver): pundit opinion ("Cramer says..."),
  "stock up/down today" price commentary, technical-rating blurbs
  (RSI or relative-strength), and notes that merely restate consensus.

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
