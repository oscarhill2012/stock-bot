"""Build a Gemini ``ThinkingConfig`` from config, selecting the correct knob.

Why this exists
---------------
Gemini exposes two *mutually exclusive* ways to control how many internal
reasoning ("thinking") tokens a model spends:

* ``thinking_budget`` — an integer token ceiling.  This is the Gemini 2.5
  family's knob (and the one the strategist's ``gemini-2.5-pro`` still uses).
* ``thinking_level`` — a coarse effort enum (``minimal`` / ``low`` /
  ``medium`` / ``high``).  This is the Gemini 3 family's knob.

Sending BOTH in the same request is rejected by the Gemini 3 API with an HTTP
400 ("thinking_budget and thinking_level are not supported together"), and on a
Gemini 3 model the endpoint expects the *level* form — so a 2.5-style
``thinking_budget`` alone is not the right shape there either.

Rather than parse the model string to decide which knob applies (which would
also trip the ``tests/contract/test_no_hardcoded_models.py`` contract that
forbids model-name literals in ``src/``), the *choice of knob is a config
decision*: each agent sets exactly one of the two in its config block, and this
helper turns that into the right ``ThinkingConfig``.  Setting both is a
misconfiguration and fails loudly rather than reaching the API as a 400.
"""
from __future__ import annotations

from google.genai import types as genai_types


def build_thinking_config(
    *,
    thinking_budget: int | None,
    thinking_level:  str | None,
) -> genai_types.ThinkingConfig | None:
    """Translate the configured thinking knob into a ``ThinkingConfig``.

    Exactly one of the two knobs may be set.  ``thinking_level`` is the Gemini
    3 form; ``thinking_budget`` is the Gemini 2.5 form.  They are mutually
    exclusive — passing both is exactly the request shape Gemini 3 rejects with
    an HTTP 400, so we refuse it up front rather than let it reach the API.

    Parameters
    ----------
    thinking_budget:
        Integer thinking-token ceiling for the Gemini 2.5 family, or ``None``.
        ``0`` disables thinking and ``-1`` requests dynamic thinking; both are
        passed through verbatim (``0`` is *not* treated as "unset").
    thinking_level:
        Gemini 3 effort level — one of ``"minimal"``, ``"low"``, ``"medium"``,
        ``"high"`` (case-insensitive), or ``None``.

    Returns
    -------
    genai_types.ThinkingConfig | None
        A config carrying whichever single knob was set, or ``None`` when
        neither is set (leaving the model's native thinking behaviour
        untouched — equivalent to omitting ``thinking_config`` entirely).

    Raises
    ------
    ValueError
        If both knobs are set (mutually exclusive), or if ``thinking_level`` is
        not a recognised level.
    """

    # Both knobs together is precisely the Gemini 3 400 — fail before the call.
    if thinking_budget is not None and thinking_level is not None:
        raise ValueError(
            "thinking_budget and thinking_level are mutually exclusive — set "
            "exactly one (thinking_level for Gemini 3 models, thinking_budget "
            "for Gemini 2.5); Gemini 3 rejects both together with an HTTP 400."
        )

    # Gemini 3 form: coarse effort enum.  Upper-cased to match the SDK's
    # ThinkingLevel enum values (MINIMAL / LOW / MEDIUM / HIGH).  The enum
    # constructor only *warns* on an unknown value, so we validate membership
    # ourselves and raise — silent degradation is the house bug class.
    if thinking_level is not None:
        normalised = thinking_level.upper()
        valid_levels = {
            lvl.value for lvl in genai_types.ThinkingLevel
        } - {genai_types.ThinkingLevel.THINKING_LEVEL_UNSPECIFIED.value}

        if normalised not in valid_levels:
            raise ValueError(
                "thinking_level must be one of "
                f"{sorted(v.lower() for v in valid_levels)}; got {thinking_level!r}."
            )

        return genai_types.ThinkingConfig(
            thinking_level = genai_types.ThinkingLevel(normalised)
        )

    # Gemini 2.5 form: integer thinking-token ceiling.
    if thinking_budget is not None:
        return genai_types.ThinkingConfig(thinking_budget = thinking_budget)

    # Neither configured → leave the model's native thinking behaviour alone.
    return None
