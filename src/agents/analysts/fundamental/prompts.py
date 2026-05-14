"""Fundamental analyst prompt — Phase 5 (closed vocab + insider supplement).

The narrowed Fundamental LLM reads MD&A excerpts, risk-factor excerpts, and
Form 4 footnotes (prose). It also receives a structured block of insider
numerics (10b5-1 ratio, cluster flags, role rank, derivative counts) to
anchor its prose reasoning in quant context. It emits closed-vocabulary
tags only — no free text in ``key_factors``.

Runtime context is delivered via two ADK session-state keys that the
``fundamental_fetch_callback`` populates before this agent runs:

- ``fundamental_context`` — a formatted multi-ticker block containing each
  ticker's filings excerpts and insider activity (numerics + footnotes).
- ``tickers`` — the watchlist (standard pipeline state key).

These appear as ``{fundamental_context}`` and ``{tickers}`` in the rendered
instruction string so ADK's ``inject_session_state`` substitutes them at
agent-run time.
"""
from __future__ import annotations

from agents.analysts.heuristics import FundamentalVocabulary

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------
# Vocabulary tokens (single-brace) are substituted at agent-construction time
# by ``build_fundamental_instruction``.  Runtime state tokens
# ``{fundamental_context}`` and ``{tickers}`` are left intact as single-brace
# so ADK's state injector fills them each tick.
# ---------------------------------------------------------------------------

_TEMPLATE = """You are the Fundamental analyst.

For each ticker in the batch, reason over the company's filings prose
(MD&A excerpts, risk factors) AND the INSIDER ACTIVITY block (numeric flows
+ footnote prose). You must produce a structured verdict per ticker.

Each ticker's data block contains:

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

For each ticker output a JSON object with fields:
  ticker       string (must be one of the watchlist tickers)
  lean         ∈ {{bullish, bearish, neutral}}
  magnitude    ∈ [0, 1]
  confidence   ∈ [0, 1]
  rationale    string ≤160 chars naming the dominant finding
  key_factors  list of closed-vocabulary tags (≤8)
  is_no_data   true if no excerpts AND no insider activity
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
- Cluster open-market buys by multiple officers + raised guidance + confident
  tone → strongly bullish.
- Discretionary sale dominance + lowered guidance + cautious/defensive tone
  → strongly bearish.
- Treat 10b5-1 planned sales as low-signal (discount their weight).
- Treat exercise-and-hold as bullish (insider declined to sell).
- Treat exercise-and-dump as bearish.
- Conflicting inputs → neutral with low confidence.

Emit all verdicts as a top-level JSON array under the key
``fundamental_verdicts``. Each object must include a ``ticker`` field.
MUST cover ALL tickers: {tickers}

--- TICKER DATA ---
{fundamental_context}
"""


def build_fundamental_instruction(vocab: FundamentalVocabulary) -> str:
    """Render the Fundamental LLM instruction with the closed vocabulary baked in.

    Substitutes the four vocab placeholder tokens (``{guidance_options}``,
    ``{tone_options}``, ``{risk_tags}``, ``{insider_signals}``) using
    ``str.format``.  The two runtime state tokens — ``{fundamental_context}``
    and ``{tickers}`` — are left intact in the returned string; ADK's
    ``inject_session_state`` fills them each tick from session state.

    Parameters
    ----------
    vocab:
        Validated ``FundamentalVocabulary`` instance holding the four closed-
        vocabulary lists.

    Returns
    -------
    str
        The rendered instruction string.  Contains exactly two remaining
        single-brace tokens: ``{fundamental_context}`` and ``{tickers}``.
    """
    return _TEMPLATE.format(
        guidance_options=" | ".join(vocab.guidance),
        tone_options     =" | ".join(vocab.tone),
        risk_tags        =" | ".join(vocab.risks),
        insider_signals  =" | ".join(vocab.insider_signals),
        # Protect the two ADK runtime placeholders from str.format substitution
        # by passing them back as themselves.
        fundamental_context="{fundamental_context}",
        tickers            ="{tickers}",
    )
