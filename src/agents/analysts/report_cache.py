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

from data.models import (
    CompanyRatios,
    Filing,
    Form4Bundle,
    NewsArticle,
)

# ---------------------------------------------------------------------------
# Prompt-version fingerprints — bump when prompt or closed vocab changes.
# ---------------------------------------------------------------------------

#: Version string baked into every News cache entry. Bump to invalidate all
#: cached News verdicts after a prompt-template or vocabulary change.
NEWS_PROMPT_VERSION = "2026-05-14-a"

#: Version string baked into every Fundamental cache entry. Bump to invalidate
#: all cached Fundamental verdicts after a prompt-template or vocabulary change.
FUNDAMENTAL_PROMPT_VERSION = "2026-05-14-a"


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
    ``model_dump()`` dicts stored in ``state["news_data"]``.

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
        ``{"verdict": ..., "report": ...}`` on a hit; ``None`` on any miss
        (missing file, hash mismatch, version mismatch, or IO error).
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

    return {"verdict": record.get("verdict"), "report": record.get("report")}


def write_cache(
    root: Path,
    analyst: str,
    ticker: str,
    *,
    input_hash: str,
    prompt_version: str,
    verdict: dict,
    report: dict | None,
) -> None:
    """Atomically write a fresh cache entry for one ``(analyst, ticker)`` pair.

    Uses ``os.replace`` for atomicity so a crash mid-write does not leave the
    cache file in an unparseable state. Creates the parent directory tree if
    it does not yet exist.

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
    """
    path = _cache_path(root, analyst, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "input_hash":     input_hash,
        "prompt_version": prompt_version,
        "verdict":        verdict,
        "report":         report,
        "stored_at":      datetime.now(UTC).isoformat(),
    }

    # Write to a temp file then atomically replace — prevents partial writes.
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
