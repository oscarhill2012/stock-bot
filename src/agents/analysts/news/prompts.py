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

_TEMPLATE = """\
You are the news analyst for {ticker}. You do NOT react to sentiment — you
position for POST-NEWS DRIFT: the well-documented tendency of prices to
continue moving in the direction of a genuine surprise for days to weeks
after the news lands (post-earnings-announcement drift and its analogues).

Today's date and the news for {ticker} are below. Articles arrive in two
sections, pre-filtered deterministically before you see them:

- FRESH ARTICLES — not previously seen by this desk. These are your only
  candidates for a NEW surprise. Full text is provided.
- PREVIOUSLY SEEN — already assessed on earlier ticks (headlines and ages
  only). These are NOT new information. Use them solely to work out where
  you are inside an existing drift window.

{news_context}

DECISION PROCEDURE — work through these steps in order:

STEP 1 — SURPRISE CLASSIFICATION (fresh articles only).
For each fresh article, decide: is this a GENUINE SURPRISE — new, material,
company-specific information that plausibly moved (or will move)
expectations — or is it noise (rehash phrased past the filter, commentary,
minor housekeeping, already-implied follow-up)?
- A genuine surprise names specifics: numbers vs expectations, a decision,
  a contract, a regulatory outcome, guidance change.
- Noise recycles what the PREVIOUSLY SEEN section already covers, or is
  too vague to shift expectations. Classifying everything as noise is a
  perfectly good outcome — most ticks have no genuine surprise.

STEP 2 — DIRECTION. For each genuine surprise: positive or negative for
{ticker}'s equity over the coming days? Judge the SURPRISE direction (vs
expectations), not the headline's emotional tone.

STEP 3 — DRIFT POSITIONING (use the PREVIOUSLY SEEN ages).
- Fresh genuine surprise (0–1 days old): the drift window is just opening.
  Lean WITH the surprise direction. Set horizon_days to roughly 5.
- Existing drift, early/middle of the window (surprise 2–10 trading days
  ago per the PREVIOUSLY SEEN ages, no fresh contradiction): continuation
  lean is justified at REDUCED magnitude and confidence; set horizon_days
  to the remaining window (e.g. 3–15).
- Late or exhausted window (several weeks old, nothing fresh): the edge is
  gone — and stale news re-circulating without new facts mildly predicts
  REVERSAL, not continuation. Go neutral rather than chase.
- Fresh surprise CONTRADICTING an existing drift: the fresh information
  wins; re-anchor on it.

STEP 4 — NO SURPRISE AT ALL. Nothing fresh is genuine and no live drift
window exists → lean neutral with low magnitude. Do NOT manufacture a lean
from noise volume.

horizon_days is REQUIRED: the number of TRADING DAYS you expect your lean
to remain valid. ~5 for a fresh surprise; longer (up to ~20) only for
mid-window drift continuation; 1 for a neutral no-surprise verdict.

OUTPUT CONTRACT — respond ONLY with a JSON object matching the schema.
``is_no_data`` and ``report`` are REQUIRED on every call, including when \
is_no_data=true — there is no shorter legal output. Field meanings:
- ticker: string — MUST be exactly "{ticker}".
- lean: "bullish" | "bearish" | "neutral".
- magnitude: 0.0–1.0 — size of the expected drift move, discounted by how
  far into the window you already are.
- confidence: 0.0–1.0 — how sure you are a genuine surprise (or live
  drift) exists. Noise-only ticks are LOW confidence neutrals.
- is_no_data: true ONLY when the context shows "(no news available)" for
  {ticker}; key_factors is then empty, but report is STILL REQUIRED — never
  null — carrying a one-line "no news in window" summary plus the two
  drivers described below, both noting the absence.
- horizon_days: integer >= 1 — trading days the lean should hold (see
  STEP 3).
- key_factors: 0–8 tags, EXCLUSIVELY from this closed vocabulary —
  catalyst:<one of {catalyst_options}>, novelty:<one of {novelty_options}>,
  direction:<one of {direction_options}>, plus the bare tag "material" when
  the surprise is company-moving. Never invent tags outside this list.
- report: REQUIRED on EVERY call, never null — including when
  is_no_data=true (see is_no_data above).
  - report.summary: <= {summary_max} characters — state the
    surprise (or its absence), the drift-window position, and the horizon.
  - report.drivers: 2–4 entries. Give at least two, one per reasoning axis:
    (1) the SURPRISE — the genuine fresh surprise and its direction, or
    that none exists; (2) the DRIFT-WINDOW POSITION — where you are in a
    live drift window, or that none is live. Each driver: name
    <= {driver_name_max} characters; direction one of
    {{bull | bear | neutral}}; weight in [0.0, 1.0]; body
    <= {driver_body_max} characters explaining how that surprise (or the
    window position) feeds the lean. On a no-surprise / no-data tick, both
    drivers are neutral and simply record the absence.

SHAPE EXAMPLE (illustrative values only — never copy them):
{{
  "ticker": "{ticker}",
  "lean": "bullish",
  "magnitude": 0.45,
  "confidence": 0.7,
  "is_no_data": false,
  "horizon_days": 5,
  "key_factors": ["catalyst:earnings", "novelty:high",
                  "direction:positive", "material"],
  "report": {{
    "summary": "Fresh EPS beat with raised guidance is a genuine positive \
surprise; drift window opening today, positioning long for ~5 sessions.",
    "drivers": [
      {{"name": "eps_beat", "direction": "bull", "weight": 0.6,
        "body": "Reported EPS well above consensus per the fresh article."}},
      {{"name": "guidance_raise", "direction": "bull", "weight": 0.4,
        "body": "Full-year guidance lifted, extending the surprise."}}
    ]
  }}
}}

Stop emitting if you are about to repeat a token or symbol three or more times in a row.
Return the verdict as-is and never emit filler tokens.
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
