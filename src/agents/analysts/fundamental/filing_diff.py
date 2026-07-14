"""filing_diff — year-over-year paragraph diff by lexical similarity.

Supersedes the Phase 13 SHA-256 de-boilerplate filter.  A current paragraph is
dropped as a near-duplicate when its number-normalised cosine against its
best-matching prior-year paragraph meets ``dedup_cosine``.  Two additions over
a plain drop:

1. NUMERIC DELTAS — a paragraph deduped on language may still carry a materially
   changed figure (number normalisation hides it).  We compare the raw numerals
   of the current paragraph against its matched prior paragraph and surface any
   change >= ``numeric_delta_pct`` so the LLM can weigh it.
2. Near-verbatim marker — when (almost) every paragraph dedups, the documented
   "filing is near-verbatim" header fires with no body, landing the LLM in the
   quiet-bullish branch.

Pure; the LLM-facing text is deterministic given the inputs and thresholds.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from agents.analysts.fundamental.filing_similarity import (
    FILING_SIMILARITY_ALGO_VERSION,
    _cosine_vectors,
    _vectorise,
)

_logger = logging.getLogger(__name__)

# Bump when the diff assembly changes.  Combined with the similarity version so
# either moving busts the fundamental report cache and the persisted columns.
FILING_DIFF_ALGO_VERSION = f"v2+sim:{FILING_SIMILARITY_ALGO_VERSION}"

_RE_BLANK_LINES = re.compile(r"\n\n+")

# A signed decimal numeral (with thousands separators) for the numeric-delta
# detector.  Percent signs are excluded from the capture so "21.0%" yields 21.0.
_RE_NUMERAL = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _split_paragraphs(text: str) -> list[str]:
    """Split ``text`` into non-empty paragraphs on blank-line boundaries.

    Falls back to single-newline splitting when the text has no blank lines
    (common in some EDGAR extracts) so the diff still operates on chunks rather
    than the whole document.

    Parameters
    ----------
    text:
        Raw section prose.

    Returns
    -------
    list[str]
        Stripped, non-empty paragraphs in document order.
    """
    paragraphs = _RE_BLANK_LINES.split(text)
    if len(paragraphs) <= 1:
        paragraphs = text.split("\n")
    return [p.strip() for p in paragraphs if p.strip()]


def _numerals(paragraph: str) -> list[float]:
    """Extract comparable numerals from a paragraph in document order.

    Parameters
    ----------
    paragraph:
        A single paragraph of prose.

    Returns
    -------
    list[float]
        Parsed numerals (thousands separators stripped); empty if none.
    """
    out: list[float] = []
    for raw in _RE_NUMERAL.findall(paragraph):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _numeric_deltas(current_para: str, prior_para: str, threshold: float) -> list[str]:
    """Return human-readable notes for materially changed figures.

    Compares the ordered numerals of a deduped current paragraph against its
    matched prior paragraph.  Only aligned when the counts match (a differing
    count means the structure changed, in which case the paragraph would not
    have deduped anyway).  A change qualifies when ``|Δ| / |prior|`` meets
    ``threshold``.

    Parameters
    ----------
    current_para, prior_para:
        The matched paragraph pair.
    threshold:
        Minimum fractional change to surface.

    Returns
    -------
    list[str]
        Notes like ``"1.0 -> 3.0 (+200.0%)"``; empty if nothing qualifies.
    """
    cur = _numerals(current_para)
    pri = _numerals(prior_para)
    if not cur or len(cur) != len(pri):
        return []

    notes: list[str] = []
    for c, p in zip(cur, pri, strict=True):
        if p == 0.0:
            continue
        change = (c - p) / abs(p)
        if abs(change) >= threshold:
            notes.append(f"{p:g} -> {c:g} ({change * 100:+.1f}%)")
    return notes


@lru_cache(maxsize=256)
def filing_diff(
    current_text: str,
    prior_text: str,
    *,
    dedup_cosine: float,
    numeric_delta_pct: float,
    algo_version: str = FILING_DIFF_ALGO_VERSION,  # noqa: ARG001 — cache key only
    prior_period_label: str = "prior year",
) -> tuple[str, dict]:
    """Diff ``current_text`` against ``prior_text`` by lexical similarity.

    Each current paragraph is matched to its best prior paragraph by cosine.
    Paragraphs at or above ``dedup_cosine`` are dropped (near-duplicate); any
    material numeral change within them is captured separately.  Survivors are
    the genuine year-over-year change.

    Parameters
    ----------
    current_text, prior_text:
        Full section prose for the current and prior comparable filings.
    dedup_cosine:
        Cosine at/above which a paragraph counts as unchanged.
    numeric_delta_pct:
        Fractional figure change surfaced from deduped paragraphs.
    algo_version:
        Cache-key only (bump ``FILING_DIFF_ALGO_VERSION``).
    prior_period_label:
        Human label for the prior period (e.g. ``"FY2023"``).

    Returns
    -------
    tuple[str, dict]
        ``(rendered_text, stats)``.  ``stats`` keys: ``paragraphs_total``,
        ``paragraphs_dropped``, ``coverage_pct``, ``numeric_deltas`` (list),
        ``chars_in``, ``chars_out``.
    """
    prior_paragraphs   = _split_paragraphs(prior_text)
    current_paragraphs = _split_paragraphs(current_text)

    survivors:      list[str] = []
    numeric_deltas: list[str] = []
    dropped = 0

    # Pre-vectorise each side ONCE — tokenise + term-frequency Counter + squared
    # norm — so the O(M×N) inner loop below scores from cached vectors via
    # ``_cosine_vectors`` instead of re-tokenising both paragraphs on every one
    # of the M×N comparisons (the perf regression Plan 1b introduced).
    current_vectors = [_vectorise(p) for p in current_paragraphs]
    prior_vectors   = [_vectorise(p) for p in prior_paragraphs]

    for para, (a_counts, sq_a) in zip(current_paragraphs, current_vectors, strict=True):
        # Best prior match by cosine.
        best_prior = ""
        best_cos   = 0.0
        for prior_para, (b_counts, sq_b) in zip(prior_paragraphs, prior_vectors, strict=True):
            cos = _cosine_vectors(a_counts, sq_a, b_counts, sq_b)
            if cos > best_cos:
                best_cos, best_prior = cos, prior_para

        if best_cos >= dedup_cosine:
            dropped += 1
            numeric_deltas.extend(
                _numeric_deltas(para, best_prior, numeric_delta_pct)
            )
        else:
            survivors.append(para)

    total = len(current_paragraphs)
    kept  = total - dropped
    coverage_pct = round(100.0 * kept / total, 1) if total else 100.0

    # --- Assemble the LLM-facing text ---
    if total and dropped == total:
        # Every paragraph deduped: the documented quiet-bullish near-verbatim
        # marker, with no body prose.
        body = (
            f"[filing-diff vs {prior_period_label}: {total} of {total} "
            f"paragraphs removed as unchanged — filing is near-verbatim]"
        )
    else:
        header = (
            f"[filing-diff vs {prior_period_label}: {dropped} of {total} "
            f"paragraphs removed as unchanged]"
        )
        body = header + "\n\n" + "\n\n".join(survivors)

    if numeric_deltas:
        body += "\n\nNUMERIC DELTAS (figures changed inside unchanged prose):\n" + \
                "\n".join(f"  - {n}" for n in numeric_deltas)

    stats: dict = {
        "paragraphs_total":   total,
        "paragraphs_dropped": dropped,
        "coverage_pct":       coverage_pct,
        "numeric_deltas":     numeric_deltas,
        "chars_in":           len(current_text),
        "chars_out":          len(body),
    }
    return body, stats
