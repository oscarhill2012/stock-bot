# Measurement fix — autocorrelation-robust scoreboard inference

**Date:** 2026-06-23
**Scope:** strictly the eval/scoreboard *measurement* layer. No analyst logic,
prompts, or verdicts were touched. Only the standard error / t-stat / p-value
computation changed; `n` and `mean excess (bps)` are byte-identical to before.

## Problem

The analyst predictive-power scoreboard computed its t-stat / p-value with
`scipy.stats.ttest_1samp(scores, 0.0)`, which assumes **independent**
observations. The scored observations are nothing of the sort:

- for a given ticker the verdict **persists across ticks** (cache replay is
  partly deduplicated, but distinct-confidence ticks still produce runs of
  near-identical scores), and
- the +5d / +20d **forward-return windows heavily overlap**, so consecutive
  observations on one ticker share most of their realised return.

The result is strong **within-ticker serial autocorrelation** (plus some
cross-sectional correlation within a tick). Treating these as independent
understates the standard error, inflating `|t|` and shrinking p — so noise reads
as signal. The iter-11 audit estimated roughly a 4.5× significance inflation.

## What changed

| File | Change |
|------|--------|
| `src/backtest/scoreboard.py` | New `_cluster_robust_ttest(scores, cluster_labels)`. `_aggregate` gained `cluster_labels` + `inference` params; ticker labels are now threaded through a parallel `cluster_store` so each score carries its ticker. `build_analyst_scoreboard` gained an `inference` parameter. |
| `src/backtest/settings.py` | New setting `scoreboard_inference` (`"cluster_ticker"` \| `"naive"`, default `"cluster_ticker"`). |
| `config/backtest_settings.json` | Added `"scoreboard_inference": "cluster_ticker"`. |
| `config/README.md` | Documented `scoreboard_inference`. |
| `scripts/backtest_scoreboard.py` | Now passes `inference`, `neutralise_by` and `primary_horizon_by_analyst` from config; added a `--neutralise-by` override flag for A/B re-scores. (Previously the script ignored config and always used the function default `universe` + naive.) |
| `tests/unit/backtest/test_scoreboard.py` | New `TestClusterRobustInference` (4 tests). |

## Method chosen: cluster-robust (sandwich, CR1) SE clustered by ticker

The sample mean is the OLS estimator from regressing scores on a constant. The
cluster-robust variance clusters residuals by ticker:

```
beta_hat   = mean(x)
u_i        = x_i − beta_hat
meat       = Σ_g ( Σ_{i∈g} u_i )²        (sum over ticker clusters g)
Var(mean)  = [G/(G−1)] · meat / N²       (CR1 finite-sample correction)
t          = beta_hat / sqrt(Var(mean)),  df = G − 1  (Student-t)
```

**Why this one (over a block bootstrap or two-way clustering):**

- It directly targets the **dominant** non-independence: within-ticker temporal
  autocorrelation from verdict persistence and overlapping return windows.
- It is **deterministic** — no seed, no iteration count, reproducible re-scores.
- It **degrades gracefully to the naive test** on genuinely i.i.d. data: with
  singleton clusters there are no cross terms, `meat = Σ u_i²`, `G = N`, and the
  variance collapses to `Σ u_i² / (N(N−1))` — exactly `ttest_1samp`'s SE, with
  df `= N−1`. So the correction can never spuriously move a result that had no
  autocorrelation to correct (verified by a dedicated test).

**Trade-off / what it does NOT capture:** clustering by ticker leaves the
**cross-sectional** (within-tick, across-ticker) correlation uncorrected. The
fully-correct estimator is two-way clustering (ticker AND tick). I deliberately
chose one-way ticker clustering because (a) the within-ticker channel is by far
the larger one here — overlapping 20-day windows on a persistent verdict — and
(b) two-way clustering with these cluster counts risks a non-PSD variance and
adds complexity for a second-order correction. Ticker clustering already moves
both headline findings across the decision boundary; two-way would only widen
the SEs further, never tighten them, so the qualitative verdict is unchanged.
This is recorded as a known limitation; revisit if a headline result sits right
on the 5% line and the cross-sectional channel is suspected to matter.

## Demonstration that the fix works (not just runs)

`TestClusterRobustInference` Monte-Carlo's a **true-null** panel (population mean
= 0) with strong within-ticker autocorrelation (each ticker = a shared random
level + small noise), over 600 trials at α = 0.05:

```
naive   false-positive rate = 0.570   (nominal 0.05)   ← badly over-sized
cluster false-positive rate = 0.063   (nominal 0.05)   ← restored
```

The naive test rejects a true null **57%** of the time; the cluster-robust test
**6.3%**, ~nominal. A second test asserts exact agreement with `ttest_1samp` on
i.i.d. (singleton-cluster) data; a third asserts the cluster-robust `|t|` is
strictly smaller than naive under autocorrelation; a fourth threads it through
the full `build_analyst_scoreboard` pipeline and checks `n` / mean are unchanged.

## Before vs after — the two headline cells

Re-scored from the on-disk `db.sqlite` + golden cache (no pipeline re-run),
`neutralise_by="universe"`. `n` and mean are identical across modes by
construction.

| run | cell | n | mean (bps) | naive t / p | **cluster t / p** | survives? |
|-----|------|---|-----------:|-------------|-------------------|-----------|
| baseline-2025-09 | technical +20d all | 608 | −64.4 | −2.26 / 0.024 | **−1.08 / 0.296** | **No** |
| iran-conflict-2026-02 | technical +5d all | 625 | +27.7 | +3.12 / 0.002 | **+2.04 / 0.055** | **No (borderline)** |

The naive figures reproduce the audit's reported headline numbers exactly,
confirming the re-score is faithful.

### Full corrected scoreboards (cluster_ticker, universe), `technical`

**baseline-2025-09 / analysts-eval-iter-11**

| horizon | subset | n | mean (bps) | hit | t | p |
|---|---|---|---:|---|---:|---|
| +1d | all | 608 | +0.8 | 45.4% | +0.12 | 0.909 |
| +5d★ | all | 608 | +6.0 | 43.0% | +0.25 | 0.802 |
| +20d | all | 608 | −64.4 | 45.0% | −1.08 | 0.296 |
| +20d | bearish | 85 | −263.7 | 60.0% | −0.64 | 0.541 |

**iran-conflict-2026-02 / analysts-eval-iter-11**

| horizon | subset | n | mean (bps) | hit | t | p |
|---|---|---|---:|---|---:|---|
| +1d | all | 625 | +7.2 | 54.4% | +1.75 | 0.097 |
| +5d★ | all | 625 | +27.7 | 57.4% | +2.04 | 0.055 |
| +5d★ | bearish | 209 | +39.3 | 57.9% | +1.94 | 0.070 |
| +20d | all | 625 | +19.2 | 50.9% | +0.42 | 0.679 |

(Full per-analyst tables are reproducible with
`PYTHONPATH=src .venv/bin/python -m scripts.backtest_scoreboard --run-dir <run> --window <window> --neutralise-by universe`.)

## Universe vs sector neutralisation

**Finding: sector data is NOT populated in the iter-11 artefacts.** Both windows'
`store.sqlite` have `company_ratios.sector = NULL` for all 600 rows (0 non-null).
Commit `b15d076` added a *static sector map* in the data ingestion layer
(`src/data/sector_map.py`), but the iter-11 caches were fetched **before** that
commit and have not been refetched. Re-scoring under `neutralise_by="sector"`
therefore degrades to `universe` for every ticker (with a per-ticker WARNING).

Empirically confirmed: re-scoring both runs under `universe` vs `sector`
(cluster_ticker) produced **0 / 27 differing cells** in each window — the two
modes are bit-identical here.

**Recommendation:** do **not** change the `scoreboard_neutralise_by` default
based on these runs — there is nothing to compare. To get a real
universe-vs-sector comparison, the per-window golden caches must first be
**refetched** so `company_ratios.sector` is populated (a data-layer action,
outside this measurement-layer fix's scope). Once refetched, re-run the A/B with
`--neutralise-by sector` vs `--neutralise-by universe` and decide then. The
config default remains `universe` (left untouched).

## Verdict

**Neither headline finding survives honest inference.** The baseline
"significantly anti-predictive" +20d-all result collapses to p=0.30 (clearly
insignificant), and the iran "robustly predictive" +5d-all result falls to
p=0.055 — over the conventional 5% threshold and no longer "robust". Both were
artefacts of treating autocorrelated observations as independent.

## New config setting

- `scoreboard_inference`: `"cluster_ticker"` (default) | `"naive"`. Documented in
  `config/README.md`.
