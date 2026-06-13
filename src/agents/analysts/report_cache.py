"""Hash-based LLM report cache — memoises (verdict, report) on input identity.

Cache layout: ``<root>/<analyst>/<ticker>.json``. Each file is a single-entry
record; the next miss overwrites. ``<root>`` is read from
``config/analysts.json`` -> ``cache.directory`` and is always under the
gitignored ``cache/`` tree.

The cache key is ``(input_hash, prompt_version)``:

- ``input_hash``       — blake2b digest of the analyst's view of the world for
                         this ticker (article URL+published tuples for News;
                         ratios + filing accession numbers + Form 4 records
                         for Fundamental).
- ``prompt_version``   — short string baked into the analyst module; bump
                         when the prompt template or closed vocabulary
                         changes to invalidate every cached entry.

Both pieces must match for a hit. Anything else is a miss -> LLM is called
-> cache is overwritten with the fresh ``(verdict, report)``.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import blake2b
from pathlib import Path
from typing import Any

from agents.analysts.fundamental.fetch import _bundle_from_flat_lists
from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.prompts import build_news_instruction
from data.models import (
    CompanyRatios,
    Filing,
    Form4Bundle,
    NewsArticle,
)

# ---------------------------------------------------------------------------
# Prompt-version fingerprints — auto-derived from the rendered instruction
# ---------------------------------------------------------------------------
# Each constant is a 6-byte blake2b digest of the rendered prompt
# instruction string with an ``"auto:"`` prefix.  Because the rendered
# string is built by ``build_<analyst>_instruction(vocab)``, it embeds
# (a) the prompt template body, (b) the closed-vocab lists from
# ``analyst_heuristics.json``, and (c) the rationale char-cap from
# ``analysts.json::output_caps``.  Any change to any of those three
# automatically flips the version -> every cached entry written under the
# old version is treated as a miss and is overwritten on the next LLM call.
#
# Rationale:
#   Hand-maintained version strings rot — a contributor editing a prompt
#   template has no structural prompt to bump the constant.  Forgetting to
#   bump silently serves stale verdicts generated under the old prompt.
#   Auto-derivation removes the human-discipline failure mode entirely.
#   See backlog entry [[B23]] for the design discussion.
# ---------------------------------------------------------------------------

def _derive_prompt_version(instruction: str) -> str:
    """Compute the cache-key version fingerprint for a rendered prompt.

    Parameters
    ----------
    instruction:
        The fully-rendered instruction string returned by
        ``build_<analyst>_instruction(vocab)``.  Hashing the rendered
        string (rather than the template plus a reference vocab) means a
        change to the template, the closed-vocab lists, or the char-cap
        substitutions all flow into the hash through a single channel.

    Returns
    -------
    str
        A string of the form ``"auto:<12-hex-chars>"`` — a 6-byte
        blake2b digest with a literal ``"auto:"`` prefix that lets
        humans see at a glance the version was machine-derived rather
        than hand-set.  6 bytes is plenty: collision-resistance is
        irrelevant here (we only need inequality with the prior value)
        and a longer digest is too long for the eye to scan.
    """
    return f"auto:{blake2b(instruction.encode(), digest_size=6).hexdigest()}"


# Render each analyst's instruction once at import time using the heuristics
# file's closed-vocab lists, then hash the result.  The prompt builders are
# imported normally at the top of this file (A-096) — the import cycle that
# previously forced an importlib filesystem-loader workaround is gone now that
# the news/fundamental package __init__ files no longer eagerly re-export
# their .agent modules.

def _compute_version_constants() -> tuple[str, str]:
    """Render both analyst instructions and return their version fingerprints.

    Kept as a private function so the version-derivation logic is contained in
    one place and the module-level constants remain simple assignments.

    Returns
    -------
    tuple[str, str]
        ``(news_version, fundamental_version)`` — both in
        ``"auto:<digest>"`` format as returned by ``_derive_prompt_version``.
    """
    heuristics = load_heuristics()

    news_version = _derive_prompt_version(
        build_news_instruction(heuristics.news_vocabulary)
    )
    fundamental_version = _derive_prompt_version(
        build_fundamental_instruction(heuristics.fundamental_vocabulary)
    )

    return news_version, fundamental_version


_news_ver, _fundamental_ver = _compute_version_constants()

#: Version string baked into every News cache entry.  Auto-derived from
#: the rendered News prompt at module import time — see
#: ``_derive_prompt_version`` above.
NEWS_PROMPT_VERSION: str = _news_ver

#: Version string baked into every Fundamental cache entry.  Auto-derived
#: from the rendered Fundamental prompt at module import time.
FUNDAMENTAL_PROMPT_VERSION: str = _fundamental_ver


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _digest(payload: Any) -> str:
    """Return a hex blake2b digest of a JSON-serialised payload.

    Serialises the payload to JSON with sorted keys so that dict key ordering
    does not affect the digest. The ``default=str`` fallback handles
    ``datetime`` and ``date`` objects.

    Parameters
    ----------
    payload:
        Any JSON-serialisable object (dict, list, str, number, etc.).

    Returns
    -------
    str
        Hex string prefixed with ``"blake2b:"`` for legibility in cache files.
    """
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"blake2b:{blake2b(blob, digest_size=16).hexdigest()}"


# ---------------------------------------------------------------------------
# Hash functions — one per analyst domain
# ---------------------------------------------------------------------------

def news_hash_inputs(articles: list[NewsArticle]) -> str:
    """Hash the News analyst's view of the world for one ticker.

    The hash is sensitive to the set of article (URL, published_at) pairs
    only — summary text drift does not bust the cache, but a new article
    rolling in or an old one rolling out does. The list is sorted before
    hashing so that ordering is irrelevant.

    Accepts both ``NewsArticle`` instances (attribute access) and plain dicts
    (key access) so it works whether the caller passes typed objects or the
    ``model_dump()`` dicts stored in ``state["temp:news_data"]``.

    Parameters
    ----------
    articles:
        List of ``NewsArticle`` instances or ``model_dump()`` dicts for one
        ticker.

    Returns
    -------
    str
        Blake2b hex digest over the sorted (url, published_at) pairs.
    """
    items = sorted(
        (
            # Support both attribute and dict access.
            a.url if hasattr(a, "url") else a.get("url", ""),
            (
                a.published_at.isoformat()
                if hasattr(a, "published_at") and hasattr(a.published_at, "isoformat")
                else (a.published_at if hasattr(a, "published_at") else a.get("published_at", ""))
            ),
        )
        for a in articles
    )
    return _digest(items)


def fundamental_hash_inputs(
    ratios: CompanyRatios,
    filings: list[Filing],
    insider: Form4Bundle,
) -> str:
    """Hash the Fundamental analyst's view of the world for one ticker.

    Ratios floats are rounded to 4 decimal places so insignificant jitter
    (e.g. ``pe = 36.23879 -> 36.23880``) does not bust the cache. Filings are
    keyed by accession number; insider trades by ``(name, date, shares,
    price_per_share)``; derivatives by ``(name, date, transaction_code)``.

    ``price_per_share`` is optional in ``InsiderTrade`` — ``None`` is preserved
    as-is so that a ``None -> float`` change still busts the cache correctly.

    Parameters
    ----------
    ratios:
        Validated ``CompanyRatios`` for the ticker.
    filings:
        List of recent SEC filings (10-K, 10-Q, 8-K).
    insider:
        ``Form4Bundle`` containing common-stock trades and derivatives.

    Returns
    -------
    str
        Blake2b hex digest over the combined payload.
    """
    # Round all float values in ratios to 4 dp so minor yfinance jitter does
    # not produce a different digest between back-to-back ticks.
    ratios_payload = {
        k: (round(v, 4) if isinstance(v, float) else v)
        for k, v in ratios.model_dump().items()
    }

    # Filings are keyed by accession number — content changes are irrelevant
    # because the accession number is stable once the filing is indexed.
    filings_accessions = sorted(f.accession_no for f in filings)

    # Insider trades keyed by (name, date, shares, price). price_per_share is
    # Optional[float]; None is left as-is rather than defaulting to 0.0 so
    # a None-to-float transition is still a cache miss.
    insider_trades = sorted(
        (
            t.insider_name,
            t.transaction_date.isoformat(),
            t.shares,
            round(t.price_per_share, 2) if t.price_per_share is not None else None,
        )
        for t in (insider.trades if insider else [])
    )

    # Derivatives keyed by (name, date, transaction_code).
    insider_derivatives = sorted(
        (
            d.insider_name,
            d.transaction_date.isoformat(),
            d.transaction_code,
        )
        for d in (insider.derivatives if insider else [])
    )

    payload = {
        "ratios":               ratios_payload,
        "filings":              filings_accessions,
        "insider_trades":       insider_trades,
        "insider_derivatives":  insider_derivatives,
    }
    return _digest(payload)


# ---------------------------------------------------------------------------
# Disk IO
# ---------------------------------------------------------------------------

def _cache_path(root: Path, analyst: str, ticker: str) -> Path:
    """Return the path to the cache file for one ``(analyst, ticker)`` pair.

    Parameters
    ----------
    root:
        Cache root directory (e.g. ``Path("cache/reports")``).
    analyst:
        Analyst subdirectory name (``"news"`` or ``"fundamental"``).
    ticker:
        Ticker symbol — upper-cased for consistent file names.

    Returns
    -------
    Path
        Absolute-or-relative path to ``<root>/<analyst>/<TICKER>.json``.
    """
    return root / analyst / f"{ticker.upper()}.json"


def read_cache(
    root: Path,
    analyst: str,
    ticker: str,
    *,
    input_hash: str,
    prompt_version: str,
) -> dict | None:
    """Load the cache entry iff both ``input_hash`` and ``prompt_version`` match.

    On any IO or parsing error the function returns ``None`` rather than
    raising — the LLM call is always the safe fallback.

    The returned dict includes ``originating_as_of`` (ISO-8601 string or
    ``None``) so callers can log "this verdict was first computed at tick T1
    and is being served at tick T2" through the audit telemetry.

    Parameters
    ----------
    root:
        Cache root directory.
    analyst:
        Analyst subdirectory name.
    ticker:
        Ticker symbol.
    input_hash:
        Expected hash of the analyst's input data for this tick.
    prompt_version:
        Expected prompt-version fingerprint.

    Returns
    -------
    dict | None
        ``{"verdict": ..., "report": ..., "originating_as_of": ...}`` on a
        hit; ``None`` on any miss (missing file, hash mismatch, version
        mismatch, or IO error).
    """
    path = _cache_path(root, analyst, ticker)
    if not path.exists():
        return None

    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    # Both key fields must match — a version bump invalidates all entries.
    if record.get("input_hash") != input_hash:
        return None
    if record.get("prompt_version") != prompt_version:
        return None

    return {
        "verdict":           record.get("verdict"),
        "report":            record.get("report"),
        "originating_as_of": record.get("originating_as_of"),
    }


def write_cache(
    root: Path,
    analyst: str,
    ticker: str,
    *,
    input_hash: str,
    prompt_version: str,
    verdict: dict,
    report: dict | None,
    originating_as_of: datetime | None = None,
) -> None:
    """Atomically write a fresh cache entry for one ``(analyst, ticker)`` pair.

    Uses ``os.replace`` for atomicity so a crash mid-write does not leave the
    cache file in an unparseable state. Creates the parent directory tree if
    it does not yet exist.

    ``originating_as_of`` records the tick's historical clock at write
    time.  Cache hits during later ticks expose this via ``read_cache`` so
    the audit telemetry can surface "this verdict was originally computed
    under a different as_of" — informational, not a hard filter (same
    inputs imply same verdict, by construction of the input_hash).

    Parameters
    ----------
    root:
        Cache root directory.
    analyst:
        Analyst subdirectory name.
    ticker:
        Ticker symbol (upper-cased in the resulting file name).
    input_hash:
        Blake2b digest of this tick's input data.
    prompt_version:
        Prompt-version fingerprint baked into the module.
    verdict:
        Verdict dict (everything in the ``AnalystVerdict`` minus ``report``).
    report:
        Optional ``AnalystReport`` dict, or ``None`` if no report was produced.
    originating_as_of:
        The tick's ``as_of`` at the moment the verdict was computed.
        Stored in the JSON payload for later retrieval by ``read_cache``.
    """
    path = _cache_path(root, analyst, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "input_hash":        input_hash,
        "prompt_version":    prompt_version,
        "verdict":           verdict,
        "report":            report,
        "originating_as_of": originating_as_of.isoformat() if originating_as_of else None,
        "stored_at":         datetime.now(UTC).isoformat(),
    }

    # Write to a temp file then atomically replace — prevents partial writes.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Fundamental hash helper — shared between fundamental/agent.py (legacy
# batch path) and fundamental/per_ticker.py (Phase 9 per-ticker path).
# Defined here rather than in either agent module to avoid a circular import
# between per_ticker.py and agent.py once agent.py no longer imports
# anything from per_ticker.py.
# ---------------------------------------------------------------------------

def fundamental_hash_inputs_from_dict(ticker: str, triad: dict) -> str:
    """Reconstruct typed objects from a per-ticker state dict and hash them.

    The fetch agent stores ``ratios`` as a ``CompanyRatios.model_dump()``
    dict (or ``None`` on failure), ``filings`` as a list of
    ``Filing.model_dump()`` dicts, and insider data as two flat lists
    (``insider_trades`` + ``insider_derivative_trades``) of serialised dicts
    (Phase 7 unified emission shape).  This function re-validates all stored
    dicts so ``fundamental_hash_inputs`` receives the proper typed objects.

    Parameters
    ----------
    ticker:
        Ticker symbol — used as the ``CompanyRatios`` fallback dict key.
    triad:
        Per-ticker slice from ``state["temp:fundamental_data"]``.

    Returns
    -------
    str
        Blake2b hex digest over the combined fundamental input payload.
    """
    ratios_dict = triad.get("ratios") or {"ticker": ticker}
    filings_raw = triad.get("filings") or []

    # Reconstruct the typed Form4Bundle from the Phase 7 flat lists.
    # Invalid rows are silently dropped; see _bundle_from_flat_lists for
    # the suppression policy (ValidationError only — real bugs surface loudly).
    insider_obj = _bundle_from_flat_lists(
        raw_trades=triad.get("insider_trades") or [],
        raw_derivatives=triad.get("insider_derivative_trades") or [],
    )

    ratios  = CompanyRatios.model_validate(ratios_dict)
    filings = [
        Filing.model_validate(f) if isinstance(f, dict) else f
        for f in filings_raw
    ]

    return fundamental_hash_inputs(ratios, filings, insider_obj)
