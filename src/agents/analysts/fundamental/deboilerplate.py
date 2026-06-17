"""MD&A de-boilerplate filter — paragraph-level SHA-256 diff.

Removes paragraphs from a current filing's MD&A that are identical (or
near-identical after normalisation) to the prior-year filing's MD&A.  This
strips legal boilerplate, forward-looking-statement preambles, and unchanged
section headers so the analyst LLM sees only the portions of the text that
actually changed year-over-year.

Algorithm (version "v1")
------------------------
1. Split each text on two-or-more consecutive newlines (paragraph boundaries).
   Fallback: if the text contains no blank lines, split on single newlines.
2. Normalise each paragraph: lowercase, strip punctuation (keeping word
   characters and whitespace, preserving digits), collapse internal whitespace.
3. SHA-256 the normalised form to produce a paragraph fingerprint.
4. Build a set of prior-year fingerprints.
5. Keep every paragraph from the current text whose fingerprint does NOT appear
   in the prior-year set, preserving original order.
6. Prepend a one-line header that names the prior filing and the diff stats.

Memoisation
-----------
The function is memoised with ``functools.lru_cache(maxsize=256)`` on the
tuple ``(current_text, prior_text, algo_version)``.  All arguments are
immutable strings, so this is safe.  The returned ``stats`` dict should NOT
be mutated by callers.

Exports
-------
``deboilerplate_mda``       — the main entry point.
``MDA_DEBOILERPLATE_ALGO_VERSION`` — bump this when the algorithm changes
                                     to invalidate all cached entries.
"""
from __future__ import annotations

import hashlib
import logging
import re
from functools import lru_cache

_logger = logging.getLogger(__name__)

# Increment this string whenever the paragraph-diffing logic changes.
# It is included in the ``fundamental_hash_inputs`` digest so that any
# algorithm bump automatically busts every cached analyst report.
MDA_DEBOILERPLATE_ALGO_VERSION = "v1"

# Regex patterns compiled once at module load for performance.
_RE_BLANK_LINES    = re.compile(r"\n\n+")          # two or more consecutive newlines
_RE_STRIP_PUNCT    = re.compile(r"[^\w\s]")         # non-word, non-whitespace chars
_RE_COLLAPSE_SPACE = re.compile(r"\s+")             # one or more whitespace chars


def _split_paragraphs(text: str) -> list[str]:
    """Split ``text`` into paragraphs on blank-line boundaries.

    Tries double-newline splitting first.  If the result is a single-element
    list (the text has no blank lines — common in some EDGAR extracts), falls
    back to single-newline splitting so the diff still operates on meaningful
    chunks rather than the whole document as one fingerprint.

    Parameters
    ----------
    text:
        Raw MD&A text (may contain any whitespace).

    Returns
    -------
    list[str]
        Non-empty paragraph strings in document order.
    """
    # Primary split: blank lines (two or more newlines).
    paragraphs = _RE_BLANK_LINES.split(text)

    if len(paragraphs) <= 1:
        # Fallback: single newlines — avoids treating the whole document as
        # one big fingerprint when blank lines are absent.
        paragraphs = text.split("\n")

    # Strip each paragraph and discard empty results.
    return [p.strip() for p in paragraphs if p.strip()]


def _fingerprint(paragraph: str) -> str:
    """Return a SHA-256 hex digest of the normalised paragraph.

    Normalisation removes punctuation, lowercases, and collapses whitespace so
    that cosmetic differences (capitalisation, hyphenation style, extra spaces)
    do not create false negatives — i.e., paragraphs that are semantically
    identical but differ in typography are still treated as duplicates.

    Digits are preserved (via ``\\w`` in the regex) so that changed revenue
    figures, percentages, and dates correctly escape the filter as unique
    paragraphs.

    Parameters
    ----------
    paragraph:
        A single paragraph of text (already stripped).

    Returns
    -------
    str
        64-character lowercase hex SHA-256 digest.
    """
    # Lowercase first, then strip non-word/non-space chars, then collapse
    # internal whitespace and trim.
    lowered    = paragraph.lower()
    no_punct   = _RE_STRIP_PUNCT.sub("", lowered)
    normalised = _RE_COLLAPSE_SPACE.sub(" ", no_punct).strip()
    return hashlib.sha256(normalised.encode()).hexdigest()


@lru_cache(maxsize=256)
def deboilerplate_mda(
    current_text: str,
    prior_text: str,
    algo_version: str = MDA_DEBOILERPLATE_ALGO_VERSION,  # noqa: ARG001 — used for cache-busting only
    prior_period_label: str = "prior year",
) -> tuple[str, dict]:
    """Remove boilerplate from ``current_text`` by diffing against ``prior_text``.

    Paragraphs that are verbatim (after normalisation) matches of any paragraph
    in the prior year's MD&A are dropped.  The survivor paragraphs are rejoined
    and prefixed with a one-line diff-stats header.

    This is a pure function memoised on ``(current_text, prior_text, algo_version)``.
    The ``algo_version`` argument is used purely as a cache-busting key —
    bump ``MDA_DEBOILERPLATE_ALGO_VERSION`` whenever the algorithm changes.

    NOTE: The returned ``stats`` dict is shared across cache hits.  Callers
    must NOT mutate it.

    Parameters
    ----------
    current_text:
        Full MD&A text from the current filing (no pre-truncation).
    prior_text:
        Full MD&A text from the prior-year filing of the same form type.
    algo_version:
        Algorithm version string — included in the cache key only; the value
        is not used inside the function body.  Defaults to the module-level
        ``MDA_DEBOILERPLATE_ALGO_VERSION`` constant.
    prior_period_label:
        Human-readable label for the prior period, e.g. ``"FY2023"`` or
        ``"Q3 2023"``.  Included in the diff-stats header line.

    Returns
    -------
    tuple[str, dict]
        ``(filtered_text, stats)`` where:

        - ``filtered_text`` — the de-boilerplated MD&A text, prefixed with a
          header line summarising the diff.  Never empty: if ALL paragraphs
          match (very rare), the full current text is returned verbatim with
          a note that diffing had no effect.
        - ``stats`` — a plain dict with keys:
            - ``chars_in``         — length of ``current_text``
            - ``chars_out``        — length of ``filtered_text`` (including header)
            - ``paragraphs_total`` — total paragraphs in current filing
            - ``paragraphs_dropped`` — paragraphs removed as boilerplate
            - ``coverage_pct``     — percentage of paragraphs retained
              (0–100 float, rounded to 1 dp)
    """
    # --- Build prior-year fingerprint set ---
    prior_paragraphs = _split_paragraphs(prior_text)
    prior_hashes: set[str] = {_fingerprint(p) for p in prior_paragraphs}

    # --- Filter current paragraphs ---
    current_paragraphs = _split_paragraphs(current_text)
    survivors: list[str] = []
    dropped   = 0

    for para in current_paragraphs:
        if _fingerprint(para) in prior_hashes:
            dropped += 1
        else:
            survivors.append(para)

    # --- Fallback: if everything was filtered, return original text with note ---
    if not survivors:
        _logger.warning(
            "deboilerplate_mda: all %d paragraphs matched prior-year — "
            "returning full current text unchanged",
            len(current_paragraphs),
        )
        header = (
            f"[de-boilerplate: no unique paragraphs found vs {prior_period_label} — "
            f"full text shown]"
        )
        filtered = header + "\n\n" + current_text
    else:
        header = (
            f"[de-boilerplate vs {prior_period_label}: "
            f"{dropped} of {len(current_paragraphs)} paragraphs removed as unchanged]"
        )
        filtered = header + "\n\n" + "\n\n".join(survivors)

    # --- Compute coverage percentage ---
    total = len(current_paragraphs)
    kept  = total - dropped
    coverage_pct = round(100.0 * kept / total, 1) if total > 0 else 100.0

    stats: dict = {
        "chars_in":           len(current_text),
        "chars_out":          len(filtered),
        "paragraphs_total":   total,
        "paragraphs_dropped": dropped,
        "coverage_pct":       coverage_pct,
    }

    return filtered, stats
