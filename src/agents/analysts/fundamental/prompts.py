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

Output ONE JSON object — a single verdict — with fields:
  ticker       string — MUST be exactly "{ticker}"
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤{rationale_max} chars naming the dominant finding
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no excerpts AND no insider activity
  report       object — see schema below; omit only when is_no_data=true.

Report schema:
  summary  string ≤{summary_max} chars of connective tissue covering the
           gestalt this tick — not a bullet list. Argue your lean.
  drivers  2-4 entries. Each driver:
    name       string ≤{driver_name_max} chars — short label for the driver
    direction  ∈ {{bull, bear, neutral}}
    weight     ∈ [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised
    body       string ≤{driver_body_max} chars explaining the driver. Do
               NOT cite source URLs; synthesise.

The report is your reasoning; the verdict is your conclusion. They must be
consistent — the lean and direction-weighted driver mix should agree.

Decision rule:
- Cluster open-market buys by multiple officers + raised guidance + confident
  tone → strongly bullish.
- Discretionary sale dominance + lowered guidance + cautious/defensive tone
  → strongly bearish.
- Treat 10b5-1 planned sales as low-signal (discount their weight).
- Treat exercise-and-hold as bullish (insider declined to sell).
- Treat exercise-and-dump as bearish.
- Conflicting inputs → neutral with low confidence.

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
    # Prompt-facing rationale cap — what we tell the LLM.  The schema in
    # ``contract/evidence.py`` accepts up to ``schema_cap(rationale_max)``
    # so the LLM's natural 1–5% character overshoot does not crash the
    # tick — see the "two-tier convention" note in ``src/config/strategist.py``.
    out_caps = get_analysts_config().output_caps

    return _TEMPLATE.format(
        guidance_options=" | ".join(vocab.guidance),
        tone_options     =" | ".join(vocab.tone),
        risk_tags        =" | ".join(vocab.risks),
        insider_signals  =" | ".join(vocab.insider_signals),
        # Char-cap placeholders — kept in sync with the schema's
        # ``Field(max_length=...)`` via the two-tier ``schema_cap()`` convention
        # so the value the LLM is told never exceeds what the schema accepts.
        rationale_max    = out_caps.verdict_rationale_max_chars,
        summary_max      = out_caps.report_summary_max_chars,
        driver_name_max  = out_caps.report_driver_name_max_chars,
        driver_body_max  = out_caps.report_driver_body_max_chars,
        # Protect the two runtime placeholders from str.format substitution
        # by passing them back as themselves.
        fundamental_context="{fundamental_context}",
        ticker             ="{ticker}",
    )
