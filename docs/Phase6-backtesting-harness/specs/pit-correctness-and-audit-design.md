# Backtest PIT correctness and audit log — design

## 1 — Scope and goal

The Phase 6 harness plus the Phase 6 data-fill spec deliver a runnable
backtest with PIT-aware providers. They do **not** prove that the data
presented to analysts at a given `as_of` is byte-identical to what the
live pipeline would have seen at that moment. This spec closes that gap.

The deliverable has two halves, tightly coupled:

1. **Structural fixes** — eliminate every confirmed leak surface uncovered
   in the deep audit (see `docs/Phase7-post-backtest-fixing/` review notes).
2. **A two-layer audit log** that lets a human reviewer independently
   verify, after the run, that no row delivered to any analyst was sourced
   from after the tick's `as_of`. The log is the acceptance test: leak-fix
   claims are not credible until the audit log is clean.

### Guiding invariant

> Data presented to analysts in a backtest at `as_of` is byte-identical to
> what they would see if the live pipeline ran at the same `as_of`. Any
> divergence is a leak and the backtest is fiction.

### Out of scope

- **LLM model knowledge cutoff.** Gemini's training data overlaps the
  backtest window; the model "knows" historical events. Unfixable without
  pre-window model snapshots. Documented in §7.
- **Social-sentiment history.** No free historical source exists. The
  `social_sentiment` cache provider stays a deterministic `None`-return
  and the audit log records it as such (not as a leak).
- **Schema reconciliation across very old cache files.** This spec assumes
  the data-fill spec has been implemented and the cache rebuilt under its
  new schema. Long-tail legacy-cache support is not in scope.

### Re-scoping checkpoint

This spec is written before the first real backtest is run. The first
backtest will surface findings that change priorities. Treat §3 (fix list)
and §4 (audit log) as a **v1 baseline** — both will be extended in a v2
revision of this spec after the first backtest's audit log is reviewed.


## 2 — Execution model

Three independent additions to the existing harness:

```
                                ┌───────────────────────────────────────────┐
                                │  (A) Strict-as_of guard                   │
                                │                                           │
                                │  STOCKBOT_STRICT_AS_OF=1                  │
                                │      │                                    │
                                │      ▼                                    │
                                │  every layer that today falls back to     │
                                │  datetime.now() instead raises            │
                                │  AsOfRequiredError                        │
                                │                                           │
                                │  Set by scripts.backtest_run; unset on    │
                                │  live runs.                               │
                                └───────────────────────────────────────────┘

                                ┌───────────────────────────────────────────┐
                                │  (B) Per-tick telemetry (always on)       │
                                │                                           │
                                │  Driver loop ──> writes one JSON file     │
                                │                  per tick under           │
                                │                  runs/<id>/audit/         │
                                │                                           │
                                │  Cheap (~5 KB/tick), summarises every     │
                                │  data-source response.                    │
                                │  Tripwire flags surface leaks at a glance.│
                                └───────────────────────────────────────────┘

                                ┌───────────────────────────────────────────┐
                                │  (C) Deep audit dump (script, on demand)  │
                                │                                           │
                                │  scripts.backtest_audit_tick              │
                                │      │                                    │
                                │      ▼                                    │
                                │  Re-plays one tick with raw-evidence      │
                                │  collection enabled:                      │
                                │   - dumps each row's PIT-key value        │
                                │   - re-fetches upstream document for      │
                                │     independent verification              │
                                │   - flags fabricated / midnight-UTC /     │
                                │     same-day-at-open rows                 │
                                │                                           │
                                │  Run once per new window (verbose; many   │
                                │  upstream API hits).                      │
                                └───────────────────────────────────────────┘
```

### Invariants

- **Strict mode is a deliberate kill switch.** Live runs do not set
  `STOCKBOT_STRICT_AS_OF`. Backtest entrypoints (`scripts.backtest_run`,
  `scripts.backtest_audit_tick`) set it before invoking any pipeline code.
- **Telemetry is mandatory.** The driver writes one telemetry record per
  tick unconditionally. A run that fails to produce a complete telemetry
  set marks itself `status="incomplete_audit"` in the manifest.
- **Deep audit is opt-in.** Re-fetches upstream documents and is therefore
  slow and quota-burning. Invoked manually per new window or after any
  PIT-related code change.
- **Verification is independent, not self-attestation.** The deep audit
  re-fetches from upstream and asserts agreement with the cached value.
  Disagreement, fabrication markers, or midnight-UTC timestamps are
  flagged — the cache's own labels are not trusted.


## 3 — Per-leak fix list

| # | Severity | Site | Action | Detail |
|---|---|---|---|---|
| 1 | CRITICAL | `src/data/timeguard.py` (new) + every wall-clock fallback site | **New + Patch** | Introduce `resolve_as_of(*, allow_wallclock: bool) -> datetime`. In strict mode, raises `AsOfRequiredError` instead of falling back. Replace all 13 confirmed `state.get("as_of") or datetime.now(...)` and `if as_of is None: as_of = datetime.now(...)` sites with calls into the guard. Live entrypoints pass `allow_wallclock=True`. |
| 2 | CRITICAL | `src/backtest/providers/price_history_cache.py` | **Patch** | Accept `phase` (or read it from state via a thread-local) and trim the same-day bar when `phase == "open"`. Default behaviour for missing `phase` is the conservative one (trim). Bar at close phase stays — the close price is public at 16:00. |
| 3 | HIGH | `src/data/providers/stats/yfinance.py::fetch_price_history` + the cache-fill path | **Patch** | Pass `auto_adjust=False` to `yf.download`/`yf.Ticker.history`; cache OHLCV plus a separate split-event table; apply adjustments at read time bounded by `as_of`. Or assert at fill time that the most-recent split date for every ticker is **before** the first split date that would alter any cached bar — fail the fill if violated. Choice deferred to plan stage. |
| 4 | HIGH | `src/data/providers/company_ratios/pit_composite.py` (planned by Phase 6 spec) | **Patch (during implementation)** | Stamp `as_of_date` with the SEC `acceptedDateTime` of the underlying 10-K/10-Q, not the fiscal period-end. The Phase 6 spec describes the provider but not the timestamp semantics; this spec pins them. |
| 5 | HIGH | `src/backtest/cache/schema.py::PoliticianTradeRow` + `store.py::read_politician_trades` | **Patch** | Migrate `disclosure_date` and `transaction_date` from `Date` to `DateTime`. Update `read_politician_trades` filter to compare timestamps. Provider-side: keep date-only values stored as midnight UTC. Cache reader adds a "next-business-day visibility" rule for date-only-stamped rows so an unknown intraday time can't leak same-day. |
| 6 | HIGH | `src/backtest/cache/fetcher.py::_already_ok` | **Patch** | Include `source_provider` in the `(window_key, ticker, domain)` skip predicate. After a `config/data.json` flip, rows written by the previous provider are no longer considered cached. Add `--refetch-domain <list>` flag to `scripts.backtest_fetch` for forced re-fill. |
| 7 | MEDIUM | `src/data/providers/{news/finnhub,news/tiingo}.py`, `src/data/providers/{insider_trades,notable_holders,filings}/edgar.py` | **Patch** | Replace silent wall-clock substitution for missing timestamps with an explicit `MissingTimestamp` marker on the row plus a structured log line. Cache writers convert the marker into a deliberate "exclude until manually reviewed" record. Audit log surfaces the count of such markers per fetch. |
| 8 | MEDIUM | `src/agents/analysts/report_cache.py` | **Patch** | Store the originating tick's `as_of` (not just `stored_at`) alongside each cache record. Cache reads still hit on `(input_hash, prompt_version)`; the originating `as_of` is **logged** in the per-tick telemetry so a reviewer can see when a hit served a verdict computed under a different `as_of`. Not a hard filter — same inputs ⇒ same verdict is still correct. |

### Plumbing pattern: `resolve_as_of`

```python
# src/data/timeguard.py

class AsOfRequiredError(RuntimeError):
    """Raised when strict mode is active and no historical clock was supplied."""


def resolve_as_of(
    candidate: datetime | None,
    *,
    allow_wallclock: bool = False,
    site: str = "<unknown>",
) -> datetime:
    """Return ``candidate`` if non-None; else either fall back to wall-clock
    (live runs) or raise (backtest runs under STOCKBOT_STRICT_AS_OF=1).

    Parameters
    ----------
    candidate:
        The ``as_of`` value provided by the caller (may be ``None``).
    allow_wallclock:
        When ``True``, fall back to ``datetime.now(tz=UTC)`` if ``candidate``
        is ``None``.  Set by live entrypoints; **never** set by backtest code.
    site:
        Short string naming the call site (e.g. ``"aggregator"``,
        ``"news_fetch"``).  Embedded in the error message so a strict-mode
        failure tells the reviewer which layer was missing its plumbing.

    Returns
    -------
    datetime
        A timezone-aware datetime.  Either ``candidate`` (when supplied) or
        the wall-clock fallback (live mode only).

    Raises
    ------
    AsOfRequiredError
        If ``candidate is None`` and either ``allow_wallclock=False`` or
        ``STOCKBOT_STRICT_AS_OF=1``.
    """
    if candidate is not None:
        return candidate

    if os.environ.get("STOCKBOT_STRICT_AS_OF") == "1" or not allow_wallclock:
        raise AsOfRequiredError(
            f"as_of is required at site={site}; wall-clock fallback disabled"
        )

    return datetime.now(tz=UTC)
```

Every site listed in §3 row 1 replaces its inline fallback with a call into
this helper. Live entrypoints (`orchestrator/tick.py`,
`agents/executor/agent.py` when invoked outside a backtest) set
`allow_wallclock=True`; everything inside the backtest call tree does not.


## 4 — Audit-log design

### 4.1 Layer 1 — per-tick telemetry (always on)

Written by the driver to `runs/<run-id>/audit/<tick-slug>.tick.json` after
the pipeline returns for each tick. Format:

```json
{
  "tick_id":              "svb-stress-2023-03-2023-03-10T09:30:00-05:00-open",
  "as_of":                "2023-03-10T09:30:00-05:00",
  "phase":                "open",
  "strict_mode":          true,

  "tripwires": {
    "wall_clock_fallback_fired":     false,
    "any_filter_key_after_as_of":    false,
    "open_tick_sameday_bar":         false,
    "midnight_utc_timestamps_seen":  false,
    "missing_timestamp_rows_seen":   false
  },

  "per_domain": {
    "price_history": {
      "provider":      "cache",
      "ticker_rows": {
        "AAPL": {
          "count":              251,
          "min_ts":             "2022-03-10T00:00:00Z",
          "max_ts":             "2023-03-09T00:00:00Z",
          "sameday_bar_seen":   false
        }
      }
    },
    "news": {
      "provider":      "cache",
      "ticker_rows": {
        "AAPL": {
          "count":                     14,
          "min_published_at":          "2023-03-04T12:11:00Z",
          "max_published_at":          "2023-03-10T09:23:00Z",
          "midnight_utc_count":        0,
          "missing_timestamp_count":   0
        }
      }
    }
  },

  "report_cache_hits": [
    {
      "analyst":            "news",
      "ticker":             "AAPL",
      "input_hash":         "blake2b:...",
      "originating_as_of":  "2023-03-10T09:30:00-05:00"
    }
  ],

  "db_writes_recorded_at": {
    "PortfolioSnapshotRow":  {"count": 1, "matches_as_of": true},
    "TickerStanceRow":       {"count": 5, "matches_as_of": true},
    "BufferEntryRow":        {"count": 1, "matches_as_of": true}
  }
}
```

Cost: ~5 KB/tick. A 20-trading-day, two-ticks/day window produces ~200 KB.
Always on; no opt-in. Manifest entry `audit_complete=true` only when every
scheduled tick produced a telemetry record.

### 4.2 Layer 2 — deep audit dump (script-invoked)

Invocation:

```bash
PYTHONPATH=src python -m scripts.backtest_audit_tick \
  --run-id  svb-stress-2023-03-<sha7> \
  --window  svb-stress-2023-03 \
  --tick    2023-03-10T09:30:00-05:00 \
  --phase   open
```

Re-runs the single tick with an `AuditingStore` decorator wrapping
`CachedDataStore`. Every cache read is captured. For each row delivered
to any analyst, the audit script:

1. Records the cached value of the row's filter-key (`published_at`,
   `filed_at`, `as_of_date`, `ts`, or the politician-trades
   `COALESCE(disclosure_date, transaction_date)`).
2. Re-fetches the upstream document (EDGAR submission index, Tiingo
   article JSON, yfinance OHLCV row) using the **live** provider, off
   the cache.
3. Asserts the cached value and the upstream value agree within
   ±60 seconds. Disagreement is flagged but not fatal — the audit
   reviewer decides.
4. Flags `fabricated_timestamp=true` if the cached value lies within
   ±60 seconds of any timestamp in `cache_runs.started_at` (suggesting
   our wall-clock fallback fired during fill).
5. Flags `midnight_utc=true` if the cached value's time component is
   `00:00:00Z`.
6. Flags `same_day_as_as_of=true` if `value.date() == tick.as_of.date()`.

Output:
- `runs/<run-id>/audit/<tick>.full.jsonl` — one line per (analyst, ticker, row).
- `runs/<run-id>/audit/<tick>.summary.md` — human-readable tripwire summary.

Example row:

```json
{
  "tick_as_of":          "2023-03-10T09:30:00-05:00",
  "analyst":             "fundamental",
  "ticker":              "AAPL",
  "domain":              "filings",
  "row_id":              "0000320193-23-000005",
  "filter_key_field":    "filed_at",
  "filter_key_value":    "2023-02-03T11:08:32Z",
  "delta_to_as_of_sec":  -3133168,
  "upstream_evidence": {
    "source":               "sec.gov/Archives/.../0000320193-23-000005-index.json",
    "accepted_datetime":    "2023-02-03T11:08:32-05:00",
    "agreement_with_cache": true
  },
  "fabricated_timestamp": false,
  "midnight_utc":         false,
  "same_day_as_as_of":    false
}
```

### 4.3 Tripwires — what the reviewer looks at first

Rolled up into `runs/<run-id>/audit/SUMMARY.md`:

```markdown
# Tripwire summary — svb-stress-2023-03

- ✅ 0 ticks fell back to wall-clock for as_of
- ✅ 0 rows delivered with filter_key > as_of
- ✅ 0 open-phase ticks saw same-day OHLCV bar
- ⚠️ 12 news rows had published_at == 00:00:00Z (date-only)
- ⚠️ 3 EDGAR filings had filed_at substituted with fill-day wall-clock
- ✅ 0 cache hits originated under a different as_of
- ✅ All DB rows recorded_at == tick.as_of
```

The reviewer reads this first; the per-row JSONL is consulted only when a
tripwire fires. **If any ❌ appears, the backtest result is not trusted.**

### 4.4 Verification principles (made explicit)

1. **No self-attestation.** Cache labels are evidence to be checked,
   never trusted directly. Layer 2 re-fetches from upstream.
2. **Fabrication detection.** Any row whose filter-key timestamp matches
   wall-clock-at-fill-time is flagged as likely fabricated by a fallback.
3. **Date-only detection.** Rows with time component `00:00:00Z` flag a
   potential same-day leak at the next open tick after that date.
4. **Cross-tick determinism.** Re-running the same `(tick_id)` must yield
   byte-identical telemetry records. Non-determinism = uncontrolled state.
5. **DB-row stamp check.** Every `recorded_at` in `db.sqlite` matches a
   tick's `as_of`. Mismatches imply a wall-clock fallback fired silently.


## 5 — Testing strategy

### 5.1 Strict-mode tests

- Unit test on `timeguard.resolve_as_of`: raises in strict mode when
  candidate is None and `allow_wallclock=False`; returns candidate
  when supplied; returns wall-clock when not strict and
  `allow_wallclock=True`.
- Integration test: backtest run with `STOCKBOT_STRICT_AS_OF=1` and a
  deliberately broken driver that omits `state["as_of"]` — assert the
  run aborts with `AsOfRequiredError`, not silently leaking.

### 5.2 Per-leak tests

For each fix in §3 rows 2–8, a regression test in
`tests/backtest/leak_regressions/`:

- `test_open_tick_excludes_sameday_bar` — driver runs an open-phase tick,
  asserts the analyst's `price_history` payload's most-recent bar is
  strictly before `as_of.date()`.
- `test_yfinance_unadjusted_or_split_aware` — fill the cache, induce a
  fake split event, assert cached close for a date before the split is
  not retroactively adjusted.
- `test_politician_same_day_disclosure_not_visible` — write a politician
  row dated `2023-03-10 16:00`; query at `2023-03-10 09:30`; assert empty.
- `test_cache_skip_includes_source_provider` — write a row under
  `source_provider="finnhub"`, flip config to `tiingo`, run fetcher,
  assert re-fetch happens.
- `test_missing_timestamp_marks_row` — provider returns a row with no
  `published_at`; cache writer stores `MissingTimestamp` marker, not a
  wall-clock substitute.
- `test_report_cache_logs_originating_as_of` — cache write under
  `as_of=T1`, read under `as_of=T2`, hit; assert telemetry records both.

### 5.3 Audit-log self-tests

- `test_telemetry_record_shape` — schema-validates a known-good record.
- `test_tripwire_detects_filter_key_after_as_of` — synthetic store
  returns a row with `published_at > as_of`; assert tripwire fires.
- `test_tripwire_detects_open_tick_sameday_bar` — same idea for OHLCV.
- `test_audit_tick_script_smoke` — runs `scripts.backtest_audit_tick`
  against a tiny fixture cache; asserts the JSONL and summary files are
  produced and parseable.

### 5.4 Existing tests

The end-to-end smoke test
(`tests/integration/backtest/test_end_to_end_smoke.py -m slow`) is
extended to assert the manifest reports `audit_complete=true` and that
no tripwire fired for the synthetic-LLM run.


## 6 — Rollout

Eight commits, each independently mergeable to `main`:

1. **`feat(data): introduce timeguard.resolve_as_of`** — new helper
   module + `AsOfRequiredError`. No call-site changes yet. Unit tests.

2. **`refactor(data): route every wall-clock fallback through timeguard`** —
   replace inline `or datetime.now(...)` patterns in the 13 sites listed
   in the deep audit. Live entrypoints pass `allow_wallclock=True`.
   Backtest entrypoints set `STOCKBOT_STRICT_AS_OF=1`.

3. **`fix(backtest): trim same-day OHLCV bar at open phase`** —
   `price_history_cache.fetch` honours `phase` and excludes the same-day
   bar at open. Regression test.

4. **`fix(providers): preserve missing-timestamp markers instead of fabricating`** —
   news/finnhub, news/tiingo, edgar/* stop substituting wall-clock for
   missing dates. Cache writers store `MissingTimestamp` markers. Audit
   log surfaces the count.

5. **`fix(backtest): cache skip predicate includes source_provider`** —
   `Fetcher._already_ok` checks `source_provider` too. Add
   `--refetch-domain` flag to `scripts.backtest_fetch`.

6. **`feat(backtest): per-tick audit telemetry`** — driver writes one
   JSON record per tick to `runs/<id>/audit/`. Manifest gains
   `audit_complete` flag. Self-tests.

7. **`feat(backtest): backtest_audit_tick deep-dump script`** — new
   CLI, `AuditingStore` decorator, upstream re-fetch and agreement
   check, tripwire summary. Smoke test.

8. **`feat(backtest): politician_trades + report_cache PIT hardening`** —
   schema migration to `DateTime` for politician disclosure/transaction;
   originating-as_of logging in `report_cache`. Schema-version bump.

Commits 1–2 are the strict-mode foundation. Commits 3–5 close the
worst-known structural leaks. Commits 6–7 add the verification layer.
Commit 8 picks off the remaining HIGH/MEDIUM items.

After commit 2, the existing test suite must still pass with strict mode
off (live default). After commit 7, the first backtest run produces a
clean audit log. Commit 8 lands before any second backtest window is
configured.

### Deferred to v2 (after first backtest)

Two items are intentionally **not** in this spec because their priority
depends on what the first backtest's audit log surfaces:

- **yfinance auto_adjust handling (§3 row 3).** The plan stage will
  choose between (a) cache unadjusted + maintain splits separately, or
  (b) assert fill-date < first-split-date. The first audit log will
  show whether retroactive adjustment is actually affecting the SVB-2023
  window.
- **`pit_composite` SEC `acceptedDateTime` semantics (§3 row 4).** The
  Phase 6 data-fill spec is still being implemented; this fix lands
  during that implementation or immediately after.

Both will be promoted into a v2 of this spec once the first audit log
exists to inform priorities.


## 7 — Future work / known unfixables

- **LLM model knowledge cutoff.** Gemini's pre-training overlaps every
  realistic backtest window. The model "knows" historical events. The
  audit log records this risk as a header note; no code fix exists short
  of pre-window model snapshots.

- **Embedding model cutoff.** Vertex `text-embedding-005` has the same
  property. Memory similarity scores during a 2023 backtest will reflect
  embeddings trained on data through ~2024. Document only.

- **Tiingo / Finnhub free-tier history depth.** Independent of leakage —
  some old windows may simply have no news rows. The audit log flags
  "zero news rows for ticker" as a coverage warning, not a leak.

- **Live-run audit log.** Out of scope. The telemetry layer would also
  be useful in production, but the deep-dump script's upstream re-fetch
  pattern doesn't translate to a live run (the upstream is the only
  source of truth in real time). Revisit if a live observability story
  is needed.


## References

- `docs/Phase6-backtesting-harness/specs/backtest-data-fill-design.md` —
  prerequisite spec; this spec runs after that one is implemented.
- `src/backtest/cache/store.py` — current PIT filter implementation.
- `src/backtest/driver.py` — tick loop where telemetry is hooked.
- `src/backtest/providers/` — cache-provider call sites that the audit
  layer wraps.
- `src/data/__init__.py`, `src/data/aggregator.py`,
  `src/agents/analysts/*/fetch.py` — wall-clock fallback sites refactored
  in commit 2.
- Memory: `project-backtest-pit-correctness-deferred` — the leak inventory
  and audit-log requirement that motivated this spec.
- Memory: `feedback-provider-switching-must-be-one-line` — the invariant
  every new provider must continue to honour.
