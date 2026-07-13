"""Self-relative filing-similarity scale summariser.

Turns a firm's current section cosine + its OWN prior cosine history into a
single deterministic sentence for the LLM — magnitude context only, deliberately
sign-free (direction comes from the diff content).  Each firm is its own
baseline, which sidesteps sector heterogeneity without any point-in-time wrinkle
(all history is already as_of-sliced by the caller).

The chronic-changer blind spot (a habitual churner looks "normal for it") is
accepted for v1: the diff still surfaces the actual changes for the LLM to
judge, so the scale never launders them into neutral.
"""
from __future__ import annotations


def _percentile(value: float, series: list[float]) -> float:
    """Return the fraction of ``series`` strictly below ``value`` (0.0–1.0).

    Parameters
    ----------
    value:
        The current cosine.
    series:
        The firm's own prior cosines (non-empty).

    Returns
    -------
    float
        Rank fraction in [0.0, 1.0].
    """
    below = sum(1 for h in series if h < value)
    return below / len(series)


def build_scale_summary(
    *,
    section_label: str,
    form_type: str,
    current_cosine: float,
    current_jaccard: float | None,
    history_cosines: list[float],
    high_pct: float,
    low_pct: float,
    min_history: int,
) -> str:
    """Build one self-relative scale sentence for the LLM context.

    Parameters
    ----------
    section_label:
        Human section name (e.g. ``"MD&A"``).
    form_type:
        Filing form type (e.g. ``"10-Q"``) — the series is form-type-specific.
    current_cosine:
        This filing's section cosine vs its prior-year pair.
    current_jaccard:
        Secondary measure, reported as a raw number if present.
    history_cosines:
        The firm's OWN prior cosines for this section + form type, already
        as_of-sliced by the caller.  May be empty.
    high_pct, low_pct:
        Percentile band cut-offs.
    min_history:
        Minimum prior points before a percentile is trustworthy.

    Returns
    -------
    str
        A single line, e.g. ``"MD&A similarity vs prior 10-Q: cosine 0.71,
        jaccard 0.63 — 12th percentile of this firm's own 10-Q history (n=11):
        changed MORE than usual for this firm."``
    """
    jac = f", jaccard {current_jaccard:.2f}" if current_jaccard is not None else ""
    head = (
        f"{section_label} similarity vs prior {form_type}: "
        f"cosine {current_cosine:.2f}{jac}"
    )

    # Thin history — hedge rather than fabricate a percentile.
    if len(history_cosines) < min_history:
        n = len(history_cosines)
        return (
            f"{head} — only {n} prior {form_type} "
            f"{'point' if n == 1 else 'points'} for this firm; "
            f"limited baseline, judge the change from the diff content."
        )

    pct = _percentile(current_cosine, history_cosines)
    n = len(history_cosines)

    if pct <= low_pct:
        band = "changed more than usual for this firm"
    elif pct >= high_pct:
        band = "changed less than usual for this firm"
    else:
        band = "typical amount of change for this firm"

    return (
        f"{head} — {round(pct * 100)}th percentile of this firm's own "
        f"{form_type} {section_label} history (n={n}): {band}."
    )
