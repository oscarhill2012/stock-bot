# Phase 7.5 — Config-as-truth spec

**Status:** v3 draft — last open decision is D1 (analyst-side lookback
values).  v2 amendments: D4 flipped (delete the schedule keys, let the
NYSE calendar own session times); D5 renames `http_timeout_seconds` →
`quiver_http_timeout_seconds`; D3 reframed as a behavioural contract on
*known* fetch sites and moved earlier in the plan to land red before
implementations.  v3 amendments: D2 defers the aggregator migration to
Phase 7.6 (which deletes the function entirely); D3 contract test scope
narrows from three sites to two; "Modified files" loses the
`src/data/aggregator.py` row.

**Plan:** [`../plans/config-as-truth-v1.md`](../plans/config-as-truth-v1.md).

**Origin:** `docs/todo-fixes.md` Group 1 (items 1.1–1.4).

---

## Goal

Make every configuration value declared in `config/*.json` the single source
of truth at runtime — no parallel hard-coded constants, no "planned" loader
fictions, no documented keys the running code silently ignores.  Backed by
behavioural contract tests that catch regression on the *known* fetch sites
(not generic AST enforcement — see D3).

Where config keys duplicate a stronger source of truth elsewhere (the NYSE
trading calendar already owns session times), the right answer is to
**delete the redundant key**, not honour it.  Config-as-truth ≠
config-as-everything.

Phase 7.5 is the **last correctness gate before the first trustworthy
backtest run.**  Group 2 of `docs/todo-fixes.md` (data-shape contracts) is
the other pre-backtest gate; Phase 7.5 deliberately does **not** include it.

---

## Background — why this is one phase

Four separate items in `docs/todo-fixes.md` share the same disease (a config
key declared but not honoured) and would all be enforced by the same
mechanism (a behavioural contract test that fails if a known fetch site
stops sourcing from config).  Specifying them separately would mean
designing the same enforcement test four times.  Sequencing them in one
phase keeps the refactor of `FetchDefaults` / `src/backtest/settings.py` /
`config/README.md` to one pass instead of three.

### Concretely — the four issues

1. **Analyst lookbacks** disagree between four call paths.  Politician
   lookback is 30 in the analyst module, 90 in the config, 90 in the
   aggregator, 30 in the backtest-fetch mirror, and (today) 90 in the
   backtest cache provider.  Notable-holders is 90 / 180 / 180 / 365.
   Insider is 30 across the live path but the backtest cache provider
   silently defaults to 90.  News is 7 everywhere except the news cache
   provider, which defaults to 30.

2. **Backtest schedule keys** are declared in `config/backtest_settings.json`
   (`ticks_per_day`, `tz`, `open_time`, `close_time`) and re-stated in
   `config/README.md`, but `src/backtest/schedule.py` ignores them entirely
   — every value is a module-level literal.  The fix is to **delete**
   `tz` / `open_time` / `close_time` and let `pandas_market_calendars`
   own session times per session (the real PIT-correct source — handles
   early-close days correctly).  Only `ticks_per_day` remains as a genuine
   policy knob.

3. **`http_timeout_seconds` is misleadingly named** — it is read by exactly
   one provider (`quiver.py:18`).  A project-wide-sounding key that
   affects one consumer is itself drift, just dressed up.  The fix is to
   rename the key to `quiver_http_timeout_seconds` to match its real
   scope, then route quiver through it.

4. **`src/backtest/settings.py` does not exist** despite
   `config/README.md:15` documenting it as the loader.  Five callers parse
   `config/backtest_settings.json` with raw `json.loads(Path(...).read_text())`.

---

## Non-goals

This phase is plumbing, not redesign.  Out of scope:

- **Heuristic-value changes** outside lookback windows (e.g. RSI thresholds,
  confidence step sizes — those belong to a different review).
- **A separate "backtest lookback" config.**  Backtest and live read the
  same key.  Adding a parallel knob would just create the same drift class
  one floor down.
- **Per-provider HTTP-timeout overrides.**  If providers need different
  timeouts, that becomes a separate spec.  For now, one global timeout for
  every provider that needs one.
- **"Every magic number in the codebase".**  The contract test is scoped
  to lookback windows and HTTP timeouts; widening the AST walk to every
  literal int would be an unbounded exercise.
- **Cache-store schema change** for `lookback_days` semantics — flat tables
  stay flat (Group 2 territory).
- **Multi-calendar support** (LSE, NSE, …) — NYSE stays hardcoded.
- **Schedule generalisation** to >2 ticks per day, pre-/post-market, etc.
- **Pydantic-config "registry" abstraction** — `BacktestSettings` is its
  own loader, same shape as `DataConfig`, no shared base class.

---

## Decisions (resolved)

### D1 — Authoritative lookback values

For each domain we propose the value the spec adopts.  Disagreeing call
sites converge on this number.  All values reflect literature defaults for
the corresponding signal type; none are designed to favour the SVB-2023
window specifically.

| Domain | Adopted value | Rationale |
|---|---|---|
| `news_lookback_days` | **7** | News loses signal fast; a 7-day window matches the existing analyst expectation and avoids over-weighting stale catalysts.  Already the value in `config/data.json`. |
| `insider_lookback_days` | **30** | Cohen–Malloy–Pomorski use 30–60d windows for "routine" filtering; 30d captures the Form 4 two-business-day filing window plus the analyst's preferred recency cliff.  Already consistent live-side; only the cache provider default disagrees. |
| `politician_lookback_days` | **90** | Ziobrowski et al. report meaningful predictive power on ~60–90d windows for congressional trades; 90 also covers the 45-day STOCK Act disclosure ceiling with margin.  This **changes** the analyst-module value from 30 to 90 — see Risk R1. |
| `notable_holder_lookback_days` | **180** | 13F filings are quarterly (90d cadence); 180 captures two cycles so a freshly-filed holder appears in two consecutive ticks before fading.  This **changes** the analyst-module value from 90 to 180 and the cache-provider default from 365 to 180 — see Risk R1. |
| `earnings_lookback_quarters` | **4** | Already consistent; promote to `FetchDefaults`. |
| `short_interest_lookback_days` | **90** | Already consistent; promote to `FetchDefaults`. |

### D2 — Read path

Two consumers, one active pattern in Phase 7.5:

- **Analyst fetch callbacks** (`smart_money/fetch.py`, `fundamental/fetch.py`)
  read `get_config().defaults.<key>` directly at call time.  They already
  bypass the aggregator, so adding a kwarg layer to them would be
  ceremony with no caller to benefit.

- **The aggregator** (`get_stock_signal_bundle`) is **deliberately not
  migrated in Phase 7.5.**  Phase 7.6 (data-shape contracts) deletes
  `src/data/aggregator.py`, `get_stock_signal_bundle`,
  `get_stock_signal_bundle_blocking`, and `StockSignalBundle`
  entirely — confirmed zero production callers across `src/agents/`,
  `src/orchestrator/`, and `src/backtest/`.  Migrating the kwargs in
  Phase 7.5 only to delete the function one phase later would be pure
  churn.  The behavioural contract test (D3 below) is therefore scoped
  to the two analyst sites only.

The cache providers in `src/backtest/providers/*_cache.py` drop their
`lookback_days: int = N` defaults and instead require the caller to pass
the value, since they only ever execute under the orchestrator/fetcher
which already knows the right number.  This forces the lookback to flow
from one declared place rather than being absorbed by a defensive default.

### D3 — Behavioural contract test (scoped to known sites; written first)

`tests/contract/test_lookbacks_sourced_from_config.py` is a **behavioural**
test, scoped explicitly to the two known analyst fetch sites
(`smart_money/fetch.py` and `fundamental/fetch.py`):

1. Monkey-patches `get_config()` to return a `DataConfig` whose
   `defaults.politician_lookback_days = 995` (and similar sentinels for the
   other domains).
2. Calls each known analyst fetch callback under that patched config
   with a mock dispatcher that captures the `lookback_days=` kwarg of
   every provider call.
3. Asserts the captured value equals the sentinel.

The aggregator (`get_stock_signal_bundle`) is excluded — see D2 above
(deleted in Phase 7.6, migrating it here would be churn).

**Scope is explicit.**  This catches regression on the two known sites
listed above; it does **not** prevent a brand-new fetch site landing
tomorrow with a hardcoded literal.  An AST walker could close that future
gap but would be brittle (false positives on every legitimate integer
literal).  We accept the narrower guarantee in exchange for clarity and
maintainability; widening to AST enforcement is a separate spec if the
project grows enough analysts to need it.

**TDD ordering.**  The contract test is written **before** the analyst
migrations (the plan now lands it as the first task after the
`BacktestSettings` / schedule / `FetchDefaults` plumbing is in place).
It fails red across both sites on commit, then each subsequent
migration commit makes one section green — TDD-correct ordering with
incremental, reviewable progress.

### D4 — Delete `tz` / `open_time` / `close_time`; keep `ticks_per_day`

We **delete** the time and tz keys rather than honour them, because they
duplicate a stronger source of truth that already exists.  Reasons:

- `pandas_market_calendars` already owns NYSE session times — including
  early-close days (day-after-Thanksgiving 13:00, Christmas Eve 13:00,
  etc.).  Honouring a config `close_time = "16:00"` would silently break
  PIT alignment on every early-close session in the cache.  This is a
  correctness bug, not a feature.
- Letting users tune `open_time` is not a knob; it's an opportunity to
  break PIT correctness with no upside.  The schedule is whatever the
  calendar says.
- `tz` is redundant — `pandas_market_calendars.schedule()` returns
  timezone-aware timestamps already.

The new `schedule.py` calls `_NYSE.schedule(start, end)` and reads
`market_open` / `market_close` per session straight from the calendar.
Holiday handling and early-close days are correct by construction.

`ticks_per_day` stays in config — it's a genuine policy knob (open vs
close vs both).  The generator validates the phase set explicitly so
typos like `["opening", "close"]` fail loudly instead of emitting an
empty schedule.

Calendar name stays hardcoded as `"NYSE"`.  Adding a `calendar` config
key would force every consumer of `pandas_market_calendars` to plumb it
through; no plausible non-NYSE use case before live deploy.  Document
the hardcode as deliberate in `src/backtest/schedule.py`'s module
docstring.

**Migration cost.**  `BacktestSettings` does *not* gain
`tz` / `open_time` / `close_time` fields.  `config/backtest_settings.json`
loses those three keys.  `config/README.md` row drops the corresponding
columns.  The `BacktestSettings` Pydantic model uses
`model_config = ConfigDict(extra="forbid")` so that the deleted keys, if
left in a stale config file, fail loudly on load rather than being
silently absorbed.

### D5 — Rename `http_timeout_seconds` → `quiver_http_timeout_seconds`

Quick audit: the existing `http_timeout_seconds` key is referenced by
exactly one provider (`politician_trades/quiver.py:18`).  Every other
provider uses framework defaults (yfinance, edgartools, finnhub-python)
that we do not modify here.

A project-wide-sounding key that affects exactly one provider is itself
drift, just relabelled.  Three options were considered:

1. **Audit every provider** and route them all through a shared timeout.
   Rejected: most providers use third-party SDKs whose timeout knobs are
   a separate refactor; this would balloon Phase 7.5's scope.
2. **Delete the key and inline `15.0`** in `quiver.py`.  Rejected:
   contradicts the config-as-truth principle for the one consumer we
   actually have.
3. **Rename the key** to `quiver_http_timeout_seconds` so the scope is
   honest, then route quiver through it.  **Adopted.**

The naming pattern (`<provider>_http_timeout_seconds`) is honest about
scope.  If a second provider needs a tunable timeout later, it adds its
own key under the same pattern — no second renaming round.

**Migration cost.**  `config/data.json` renames one key.  `DataConfig`
on `src/data/config.py` renames one Pydantic field.  `quiver.py`
references the renamed attribute.  Behavioural contract test in
`tests/contract/test_http_timeout_sourced_from_config.py` asserts the
renamed key flows through to `requests.get(timeout=...)`.

Anyone with a copy or pin of `config/data.json` outside the repo must do
the same one-line rename — flagged in Risks (R6) and the Phase 7.5
`done.md` closeout.

### D6 — Sequence the four sub-items in one PR (1.4)

The plan implements the loader, the schedule honouring, the HTTP-timeout
routing, and the lookback unification in one PR.  Rationale:

- The `BacktestSettings` loader's schema needs to include the schedule
  fields, so writing 1.4 then 1.2 would mean rewriting the loader twice.
- The lookback contract test references both analyst fetch paths
  (smart_money and fundamental); splitting it across PRs makes the test
  land before its enforcement is meaningful.
- The total diff is ~12 files; a single PR is reviewable.

Internally inside the plan, tasks are still ordered so each commit is
small and the suite passes between commits — see the plan's task order.

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `src/backtest/settings.py` | Pydantic loader + `lru_cache` singleton for `config/backtest_settings.json`, mirroring `src/data/config.py`. |
| `tests/unit/backtest/test_settings.py` | Loader validation + cache-reset hook tests. |
| `tests/contract/test_lookbacks_sourced_from_config.py` | Behavioural test asserting analysts read lookbacks from `get_config()`. |
| `tests/contract/test_http_timeout_sourced_from_config.py` | Same idea for `http_timeout_seconds`. |
| `tests/contract/test_schedule_sourced_from_config.py` | Same idea for `generate_ticks`. |

### Modified files

| Path | Change |
|---|---|
| `config/data.json` | Rename `http_timeout_seconds` → `quiver_http_timeout_seconds`. |
| `config/backtest_settings.json` | **Delete** `tz`, `open_time`, `close_time` keys; retain `ticks_per_day`. |
| `config/README.md` | Drop "(planned)" suffix on `src/backtest/settings.py`; remove the row(s) for deleted schedule keys; rename `http_timeout_seconds` → `quiver_http_timeout_seconds`. |
| `src/data/config.py` | `FetchDefaults` gains `earnings_lookback_quarters: int = 4` and `short_interest_lookback_days: int = 90`.  `DataConfig.http_timeout_seconds` renamed to `quiver_http_timeout_seconds`. |
| `src/agents/analysts/smart_money/fetch.py:38–39,89,97` | Remove `POLITICIAN_LOOKBACK_DAYS` / `HOLDER_LOOKBACK_DAYS`; read `get_config().defaults` at the call site. |
| `src/agents/analysts/fundamental/fetch.py:53,274` | Remove `_INSIDER_LOOKBACK_DAYS`; read `get_config().defaults.insider_lookback_days`. |
| `src/data/providers/politician_trades/quiver.py:18,88` | Remove `_HTTP_TIMEOUT = 15.0`; read `get_config().quiver_http_timeout_seconds` at call time. |
| `src/backtest/providers/notable_holders_cache.py:27` | Drop `lookback_days: int = 365` default; make it required. |
| `src/backtest/providers/politician_trades_cache.py:29` | Drop `lookback_days: int = 90` default; make it required. |
| `src/backtest/providers/insider_trades_cache.py:35` | Drop `lookback_days: int = 90` default; make it required. |
| `src/backtest/providers/news_cache.py:21` | Drop `lookback_days: int = 30` default; make it required. |
| `src/backtest/providers/filings_cache.py:28` | Drop `lookback_days: int = 365` default; make it required. |
| `src/backtest/schedule.py` | Rewrite to call `_NYSE.schedule(start, end)` per session; emit `market_open` / `market_close` tz-aware timestamps directly.  Drop `_OPEN_TIME` / `_CLOSE_TIME` / `_NY` literals.  Validate `ticks_per_day` against `{"open", "close"}`. |
| `scripts/backtest_fetch.py:83–91,407` | Delete `_ANALYST_LOOKBACK_DAYS` dict; read from `get_config().defaults`.  Replace raw `json.loads` with `get_backtest_settings()`. |
| `scripts/backtest_report.py:44` | Use `get_backtest_settings()`. |
| `scripts/backtest_audit_tick.py:88` | Same. |
| `scripts/debug_cache_audit.py:451` | Same. |
| `src/backtest/runner.py:185,196` | Constructor accepts a `BacktestSettings` instance directly. |
| `src/backtest/reporting.py:50–63` | `settings` parameter typed as `BacktestSettings`, not `dict`. |
| `tests/integration/backtest/test_no_silent_zero_features.py:172` | Use `get_backtest_settings()` or its test-friendly loader. |
| `tests/integration/backtest/test_backfill_smoke.py:151` | Same. |
| `tests/integration/backtest/test_end_to_end_smoke.py:256` | Same. |
| `tests/unit/backtest/test_runner_sigint.py:61` | Same. |

---

## Acceptance criteria

A reviewer can verify Phase 7.5 is complete by checking that:

1. `grep -rn "LOOKBACK_DAYS" src/agents src/data` returns nothing.
2. `grep -rn "_HTTP_TIMEOUT" src/data` returns nothing.
3. `grep -rn "_OPEN_TIME\|_CLOSE_TIME" src/backtest` returns nothing.
4. `grep -rn 'json.loads(Path("config/backtest_settings.json")' src scripts`
   returns nothing.
5. `grep -n '(planned)' config/README.md` returns nothing.
6. `grep -n '"tz"\|"open_time"\|"close_time"' config/backtest_settings.json`
   returns nothing (only `ticks_per_day` remains as a schedule key).
7. `grep -n '"http_timeout_seconds"' config/data.json` returns nothing;
   `"quiver_http_timeout_seconds"` is present exactly once.
8. `src/backtest/settings.py` exists and exports `get_backtest_settings`,
   `_reset_cache`, and `BacktestSettings`.  The Pydantic model rejects
   unknown keys (`extra="forbid"`).
9. `src/data/config.py`'s `FetchDefaults` declares
   `earnings_lookback_quarters` and `short_interest_lookback_days`;
   `DataConfig` exposes `quiver_http_timeout_seconds`.
10. Three contract tests under `tests/contract/` pass with the actual
    config; flipping a single value in a monkeypatched config makes them
    fail loudly.  The schedule test specifically asserts that an
    early-close NYSE day (e.g. 2024-11-29) yields a 13:00 close tick,
    not 16:00 — proving the calendar is the source of truth.
11. The end-to-end smoke test
    (`tests/integration/backtest/test_end_to_end_smoke.py -m slow`) still
    passes against the existing SVB-2023 cache.

---

## Risks

### R1 — Lookback value changes affect SVB-2023 results

Increasing `politician_lookback_days` from 30 → 90 and changing
`notable_holder_lookback_days` from 90 → 180 (analyst-side) means the
SmartMoney analyst will see roughly 3× and 2× more rows respectively at
every tick.  If the existing SVB-2023 backtest has any signed-off
expectations, those will shift.

**Mitigation.**  We have not yet run a backtest against ground-truth
expectations, so no signed-off baseline exists.  The change is the right
direction (smart_money was effectively running a 30d / 90d window when
config asserted 90 / 180; the literature supports 90 / 180).  Record the
shift in the Phase 7.5 done.md.

### R2 — Cache providers without defaults break existing test fixtures

Dropping `lookback_days: int = N` defaults from the cache providers means
test code that instantiated providers without that kwarg will start
failing.

**Mitigation.**  Audit `tests/integration/backtest/` and
`tests/unit/backtest/providers/` for `await fetch(ticker, as_of=...)`
patterns without `lookback_days`; pass the value through.

### R3 — Schedule consumers outside `schedule.py`

If any code outside `src/backtest/schedule.py` reads `_OPEN_TIME`,
`_CLOSE_TIME`, `_NY` from this module directly, the deletion breaks them.

**Mitigation.**  Confirmed via grep: those constants are referenced only
inside `schedule.py` (the constants are module-private with leading
underscores).  No cross-module callers.  Likewise no caller of
`generate_ticks` inspects the time-of-day of the returned ticks; they
treat the list as opaque chronological order.

### R4 — `quiver.py` config-coupling at import time

If `get_config()` is called at module import in `quiver.py`, importing the
provider before the config file is written (in tests) will explode.

**Mitigation.**  Read the timeout inside the `_fetch_trades` function body,
not at module load.  Same pattern as `_caps()` in
`fundamental/fetch.py:56`.

### R5 — Behavioural contract test masks real drift

If the monkeypatched sentinel value is shaped wrong (e.g. fails Pydantic
validation), the test may not exercise the path it claims to.

**Mitigation.**  Use plain integer sentinels that satisfy the Pydantic
type.  Each contract test has at least one positive assertion (sentinel
flows through) and one negative assertion (an unpatched run still passes).

### R6 — `quiver_http_timeout_seconds` rename is a config-key breaking change

Anyone who has copied or pinned `config/data.json` outside the repo (CI
images, downstream forks) will see `DataConfig` reject their stale
`http_timeout_seconds` key on load.

**Mitigation.**  Loud failure is the intended behaviour — better than
silently ignoring the key.  Migration is mechanical
(`s/http_timeout_seconds/quiver_http_timeout_seconds/g`).  Record the
rename in the Phase 7.5 `done.md` closeout so any downstream consumer
sees it.

### R7 — `BacktestSettings(extra="forbid")` rejects stale keys

After deleting `tz` / `open_time` / `close_time`, a backtest run against
an older `config/backtest_settings.json` (e.g. one a developer has
locally but not pulled in fresh) will fail to load with a Pydantic
validation error.

**Mitigation.**  Loud is right — stale keys silently absorbed would let
PIT-correctness regressions hide.  The error message from
Pydantic-v2's `extra="forbid"` names the offending keys so the migration
fix is obvious.  Phase 7.5 `done.md` includes a one-liner `jq` command
to strip the dead keys for anyone who runs into this.

---

## Open decisions for user agreement

After the v2 amendments, one design question remains genuinely open:

1. **D1 lookback values.**  `politician=90` (was 30 in module),
   `notable_holder=180` (was 90 in module / 365 in cache).  Both moves
   align with `config/data.json` and the literature, but they change
   observed analyst behaviour.  Alternative: keep the lower values and
   change `config/data.json` to match.  Recommendation: adopt the higher
   values per the rationale in D1.

### Resolved in v2

The following were flipped, sharpened, or otherwise nailed down after
the v1 critique:

- **D3 scope.**  Behavioural contract on **known** fetch sites only —
  not a generic AST walker that promises CI-wide drift prevention.  Test
  is written **before** the analyst migrations land so it fails red,
  then each migration commit greens its section (TDD ordering).
- **D4 flipped.**  Was "honour `tz` / `open_time` / `close_time`".  Now
  "delete them — `pandas_market_calendars` already owns NYSE session
  times, including early-close days".  Honouring the keys would silently
  break PIT alignment.  Only `ticks_per_day` remains as a real knob.
- **D5 chosen approach.**  Rename `http_timeout_seconds` →
  `quiver_http_timeout_seconds`.  Honest about scope without
  contradicting config-as-truth.  Alternative considered: delete + inline
  `15.0` (rejected because it contradicts the principle for the one knob
  we have a real config consumer for).
- **Loader pattern.**  `lru_cache` singleton + constructor injection on
  `Runner`, matching `DataConfig`.  Keeps test fixtures clean; the
  alternative (passing the dict through every CLI entrypoint) would
  drift away from `DataConfig`'s shape.

---

## Out of scope (reaffirmed)

- The PIT-correctness audit lives in
  `docs/Phase8-post-backtest-fixing/plans/pit-correctness-and-audit-v2.md`
  and waits on the first audit-log review.
- Group 2 of `docs/todo-fixes.md` (data-shape contracts) is a separate
  pre-backtest gate, not folded into Phase 7.5.
- Groups 3–5 of `docs/todo-fixes.md` (premature abstraction, dead code,
  empirically gated) are deliberately deferred.
