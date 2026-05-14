"""News analyst prompt — Phase 5 (closed-vocab, prose-only mandate).

The narrowed News LLM reads headlines and article summaries only.  Polarity
statistics (positive_score, negative_score, mention_count) that previously
lived in the prompt are removed; those numeric features flow through the
extractor channel instead.

Runtime context is delivered via two ADK session-state keys that the
``news_fetch_callback`` populates before this agent runs:

- ``news_context`` — a formatted multi-ticker block containing each ticker's
  headline list and article summaries.
- ``tickers`` — the watchlist (standard pipeline state key).

These appear as ``{news_context}`` and ``{tickers}`` in the rendered
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
# ``{tickers}`` are left intact as single-brace so ADK's state injector fills
# them each tick.  Char-cap placeholders (e.g. ``{rationale_max}``) are
# substituted at build time from ``config/analysts.json`` so the value the
# LLM is told stays in sync with the prompt-facing cap.  The schema's
# ``Field(max_length=...)`` derives a *larger* value from the same prompt
# cap via ``schema_cap()`` — see the "two-tier convention" note in
# ``src/config/strategist.py``.
# ---------------------------------------------------------------------------

_TEMPLATE = """You are the News analyst.

For each ticker in the batch, read the supplied headlines and article
summaries. Output a structured verdict per ticker using ONLY the closed
vocabulary below.

Closed vocabulary (use these tags ONLY in key_factors):

  catalyst:<type>     ∈ {catalyst_options}
  novelty:<level>     ∈ {novelty_options}
  direction:<value>   ∈ {direction_options}
  material:<bool>     when material to a long-only fund

For each ticker output a JSON object with fields:
  ticker       string (must be one of the watchlist tickers)
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤{rationale_max} chars naming the dominant catalyst
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no headlines in the window
  report       object — see schema below; omit only when is_no_data=true.

Report schema:
  summary  3-5 sentences of connective tissue covering the gestalt this
           tick — not a bullet list. Argue your lean.
  drivers  2-4 entries. Each driver:
    name       short label (4-6 words)
    direction  ∈ {{bull, bear, neutral}}
    weight     ∈ [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised
    body       2-3 sentences explaining the driver. Do NOT cite source URLs;
               synthesise.

The report is your reasoning; the verdict is your conclusion. They must be
consistent — the lean and direction-weighted driver mix should agree.

Decision rule:
- Lean ← direction: positive → bullish; negative → bearish; mixed/none → neutral.
- Magnitude ← novelty × material weight: high novelty + material → higher magnitude.
- Confidence scales with headline count; fewer than 3 articles caps confidence low.
- Conflicting direction signals across articles → mixed → neutral with low confidence.

MUST cover ALL tickers: {tickers}

--- HEADLINES & SUMMARIES ---
{news_context}
"""


def build_news_instruction(vocab: NewsVocabulary) -> str:
    """Render the News LLM instruction with the closed vocabulary baked in.

    Substitutes the three vocab placeholder tokens (``{catalyst_options}``,
    ``{novelty_options}``, ``{direction_options}``) using ``str.format``.
    The two runtime state tokens — ``{news_context}`` and ``{tickers}`` — are
    left intact in the returned string; ADK's ``inject_session_state`` fills
    them each tick from session state written by ``news_fetch_callback``.

    Parameters
    ----------
    vocab:
        Validated ``NewsVocabulary`` instance holding the three closed-
        vocabulary lists.

    Returns
    -------
    str
        The rendered instruction string.  Contains exactly two remaining
        single-brace tokens: ``{news_context}`` and ``{tickers}``.
    """
    # Prompt-facing rationale cap — what we tell the LLM.  The schema in
    # ``contract/evidence.py`` accepts up to ``schema_cap(rationale_max)``
    # so the LLM's natural 1–5% character overshoot does not crash the
    # tick — see the "two-tier convention" note in ``src/config/strategist.py``.
    out_caps = get_analysts_config().output_caps

    return _TEMPLATE.format(
        catalyst_options ="{" + " | ".join(vocab.catalysts) + "}",
        novelty_options  ="{" + " | ".join(vocab.novelty)   + "}",
        direction_options="{" + " | ".join(vocab.direction)  + "}",
        rationale_max    = out_caps.verdict_rationale_max_chars,
        # Protect the two ADK runtime placeholders from str.format substitution
        # by passing them back as themselves.
        news_context="{news_context}",
        tickers     ="{tickers}",
    )
