# Plan 1b — filing-similarity rework: results

Companion to [`plans/plan1b-filing-similarity.md`](plans/plan1b-filing-similarity.md).
Records the outcome of the branch `phase14/analyst-rework` before merge.

## Objective

Replace the Phase-13 SHA-256 exact-match paragraph diff — which treated any
routine figure roll-forward (revenue 12.1 → 13.4) as a *fully changed*
paragraph, so a filing that merely re-stated last year's language registered as
a "rewrite" and skewed the fundamental analyst systematically **bearish** — with
a number-normalised lexical similarity following Cohen, Malloy & Nguyen (2020,
"Lazy Prices"):

- **Similarity primitive** — term-frequency cosine + token-set Jaccard over
  number-normalised tokens (every numeral run collapses to one placeholder), so
  a figure roll-forward no longer masquerades as substantive change. Persisted
  per filing section (MD&A / risk / litigation).
- **Self-relative scale** — each current cosine is expressed as a percentile
  within the *firm's own* filing history, so magnitude is judged against how
  much *that* company usually changes, not an absolute threshold.
- **Division of labour** — direction comes from the diff; magnitude comes from
  the self-relative scale. The old "survival = rewrite = bearish" heuristic is
  gone.

## De-skew verification (Task 9 — operator-gated)

A 2-tick backtest (2025-09-02 / 09-03, baseline window) confirmed the skew is
broken: the fundamental analyst is **no longer uniformly bearish** — three
bullish fundamental verdicts emerged among the ticks (net still bearish in this
window, which is a genuine read, not an artefact). Gate **passed**.

**Caveat:** two ticks is a smoke test for the skew, not a calibration sample.
Whether the aggregate system leans too bullish or too bearish is a question for a
full multi-window eval with forward-return scoring (the horizon-grid scoreboard),
not for eyeballing this depth. No prompt-sentiment tuning was applied — doing so
from a two-tick, single-regime sample would overfit and re-introduce a bias in
the opposite direction, defeating the point of the rework.

## Stub-guard recompute (Task 10 + operator recompute, algo v1.1)

Operator verification exposed a precompute/render doctrine mismatch: the render
path refuses to diff sub-400-char incorporation-by-reference stubs (e.g. an MD&A
that just says "see Exhibit 13"), but the precompute had no such guard, so it
scored and persisted cosines *from* those stubs — 7% of MD&A, 27% of risk, 45%
of litigation cosines. On mixed-extraction firms these poisoned the
self-relative baseline, and both first-flagged "genuine movers" (ATO 0.455,
HRL 0.640) were re-examined against the raw excerpt lengths.

- **Fix:** algo bump v1 → v1.1 — a targeted guard that scores only full-vs-full
  pairs (both sides ≥ 400 chars), not a scoring-algorithm rework. Refreshed via a
  lightweight in-place recompute (the filing *text* cache was verified complete
  and untruncated, so no EDGAR refetch was needed — only the scoring rule
  changed).
- **Operator recompute result:** 839 rows updated; 2084 fields written + 824
  cleared to NULL = 2908 — the exact pre-fix non-null total (nothing lost or
  invented; the precompute pre-seeds all six fields to None then scores only
  full-vs-full pairs, an authoritative-clear property). Cleared 38 MD&A / 152
  risk / 222 litigation stub cosines. The guard invariant holds globally: **zero**
  non-null cosines have an own-side excerpt < 400 chars.
- **The two flagged movers, resolved:** ATO 0.455 was an artefact (full 2024 text
  vs a 209-char 2023 stub) — correctly cleared to NULL; its full-vs-full 2025
  pair (0.999) is kept. HRL litigation 0.640 was confirmed **genuine** (both years
  full text, ~1280c / ~1039c — a real litigation rewrite) and correctly kept.
- Distribution now shows the expected Lazy-Prices shape: a dense mass near 1.0
  ("no news") plus a genuine low tail (risk min 0.477, litigation min 0.520).

## Performance fix (Task 11)

The new cosine best-match matcher in `filing_diff` was O(M×N) and re-tokenised
both paragraphs on every pair (~3.0 s for one 254×262 MD&A section; a
whole-window probe timed out at two minutes). Fixed by pre-vectorising each
side's paragraphs once (tokenise → term-frequency Counter + squared-norm) and
scoring from the cached vectors via a shared `_cosine_vectors` primitive.

- **~24.5×** on the real ATO MD&A section (3.0 s → 0.125 s).
- **Byte-identical output** — verified by a sonnet task review and an opus
  re-bless (0/254 cosine differences, 0/254 best-match differences). No
  algo-version bump (the scoring is unchanged; only the implementation is faster).

## Review gates

Every task carried a sonnet spec + quality review. A whole-branch opus review at
`991961b` returned *ready to merge* with one fix applied (prompt marker copy
aligned to the emitted `[filing-diff]` prefix). The Task 10 and Task 11 deltas
each received a clean opus re-bless.

## Deferred to after merge

- **Sector data refetch.** `sector_map.py` has been repopulated for the mid-cap
  watchlist on this branch, but the persisted `company_ratios.sector` column is
  still null (the old large-cap map produced no matches). An operator refetch of
  `company_ratios` after merge repopulates it, so the scoreboard's
  sector-neutralisation lens works on the next backtest. (Secondary: the
  `sic_description` XBRL fallback also returns null for all rows — a separate
  live-fetch investigation, watchlist-agnostic.)
