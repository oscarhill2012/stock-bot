"""Shared ``apply_slack`` helper — used by both AnalystsConfig and StrategistConfig.

Previously this calculation was implemented twice, line-for-line identical,
in ``config.analysts.AnalystsConfig.schema_cap`` and
``config.strategist.StrategistConfig.schema_cap``.  Single source of truth
lives here; both methods now delegate.
"""
from __future__ import annotations


def apply_slack(prompt_cap: int, slack_percent: int) -> int:
    """Return the schema-enforced ``max_length`` for a prompt-stated cap.

    Adds ``slack_percent`` headroom and rounds up using integer math.  Integer
    math dodges floating-point surprises — ``600 * 1.1`` yields exactly
    ``660.0`` but ``200 * 1.1`` yields ``220.00000000000003`` due to binary
    representation, so the two prompt caps would round inconsistently with
    ``ceil(prompt_cap * 1.1)``.  ``(prompt_cap * (100 + slack) + 99) // 100``
    gives the same answer for both: 200 → 220, 600 → 660.

    Parameters
    ----------
    prompt_cap:
        The cap value the model is told in the prompt template.
    slack_percent:
        Extra headroom (0–100) added to the schema cap so the model has room
        before validation fails.

    Returns
    -------
    int
        ``ceil(prompt_cap * (100 + slack_percent) / 100)`` — the value passed
        to ``Field(max_length=...)``.

    Raises
    ------
    ValueError
        When ``slack_percent`` is negative.  A negative value would shrink
        the schema cap below the prompt cap and silently truncate model
        output — exactly the silent-failure pattern this audit aims to kill.
    """
    if slack_percent < 0:
        raise ValueError(f"slack_percent must be >= 0, got {slack_percent}")
    return (prompt_cap * (100 + slack_percent) + 99) // 100
