# Module audit — `src/backtest/`

Audit date: 2026-05-26. Source under audit: `src/backtest/` (driver, runner, reporting, schedule, windows, settings, decision_logger, cache/, providers/, audit/). Tests: `tests/backtest/` (3 root + audit/ + leak_regressions/) and `tests/integration/backtest/` (7 files). Scripts: `scripts/backtest_{fetch,run,report,audit_tick}.py`.

The backtest module is the largest single subtree in the codebase (~6.3k LoC src + ~2.5k LoC tests). It is structurally sound — the layered "Driver runs the live pipeline against cache-backed providers" architecture is intact and the per-window storage split is clean. The headline issues are (a) two parallel cache-row-capture mechanisms that should be one (Layer 1 inline vs Layer 2 decorator), (b) a fat finger silent-failure trio in `driver.py` / `runner.py` that swallows tick-fatal conditions, and (c) the `decision_logger.py` bare-key `state["positions"]` read that intent §7.3 explicitly flagged.

---

## F-backtest-001
- **Category:** dedupe-candidate / over-abstraction
- **Severity:** P1
- **Location:** `src/backtest/cache/store.py:858-902` (inline `_audit_record` / `_audit_enable_capture` / `_audit_drain_reads`) vs `src/backtest/audit/auditing_store.py:16-210` (`AuditingStore` decorator).
- **Evidence:** Two independent capture mechanisms exist for cache reads:
  - The inline mechanism on `CachedDataStore` is invoked at every `read_*` site (`store.py:287, 370, 453, 538, 661, 770, 855`) and drained by `driver.py:352` (`_store._audit_drain_reads()`) → feeds Layer-1 per-tick telemetry (`audit/telemetry.py:per_domain_from_store_reads`).
  - The `AuditingStore` decorator (`audit/auditing_store.py`) wraps `CachedDataStore` with explicit `read_ohlcv` / `read_news` / `read_filings` / `read_insider_trades` / `read_notable_holders` / `read_politician_trades` / `read_company_ratios` overrides that record into `self._captured` and pass through to the inner store → feeds Layer-2 deep-dump (`audit/deep_dump.py`). Used only by `scripts/backtest_audit_tick.py:92`.
  - Both buffers capture the same shape (`{domain: {ticker: [rows]}}`) of the same rows from the same call sites.
- **Intent violated:** intent §1 ("dedupe over multiple agents/abstractions doing the same thing"); contract §C Rule 1 ("one concept, one place").
- **Suggested action:** investigate — likely collapse to one mechanism. Either (a) delete the inline `_audit_*` hooks and always wrap the store with `AuditingStore` (driver enables a "telemetry" mode that drains after each tick; replay script enables a "deep" mode that drains after the single tick), or (b) delete `AuditingStore` and have `deep_dump` read from the inline buffer. The decorator approach is cleaner but requires forwarding every public `CachedDataStore` method.

## F-backtest-002
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/backtest/driver.py:203-208`, `:315-319`, `:350-355`, `:690-694`.
- **Evidence:** Four `except RuntimeError: pass` (or `return`) guards swallow "store not wired" errors during the live tick loop:
  ```
  src/backtest/driver.py:206  except RuntimeError:
  src/backtest/driver.py:208      pass                          # _audit_enable_capture
  src/backtest/driver.py:315  except RuntimeError:               # _seed_reference_prices
  src/backtest/driver.py:319      pass
  src/backtest/driver.py:353  except RuntimeError:               # _audit_drain_reads
  src/backtest/driver.py:355      cache_reads = {}
  src/backtest/driver.py:692  except RuntimeError:               # _refresh_broker_prices
  src/backtest/driver.py:694      return
  ```
  All four are commented "isolated unit tests" or "no store wired" — i.e. they exist so a Driver constructed without `set_store(...)` doesn't crash. In a real backtest run, the store IS wired and these guards should be unreachable. If a store-not-wired condition fires in production (e.g. `_store_handle.clear_store()` runs prematurely on signal handling), the tick continues with `cache_reads={}`, no reference prices, no broker price refresh — and the telemetry layer records a clean tick. The exact silent-degradation pattern user memory flags as the recurring bug class.
- **Intent violated:** test-policy §A.7 ("surface silent failures loudly"); user feedback `feedback_silent_failures_loud_tests`.
- **Suggested action:** investigate — at minimum log a warning when the guard fires. Better: don't catch in production code, instead have unit tests that construct Driver provide a stub store (or an explicit "no store" mode the Driver checks once at construction).

## F-backtest-003
- **Category:** silent-failure / bug
- **Severity:** P1
- **Location:** `src/backtest/driver.py:599`.
- **Evidence:**
  ```
  except (AttributeError, Exception) as exc:
      pipeline_exc = exc
      _log_exception_chain(exc, state["tick_id"])
  ```
  `Exception` already subsumes `AttributeError`; the tuple is redundant. More importantly, the comment block above (lines 587-591) says the intent is "catch BaseException so KeyboardInterrupt and asyncio.CancelledError propagate normally" — but the actual clause catches only `Exception`, so `BaseException` subclasses (`SystemExit`, `KeyboardInterrupt`, `CancelledError`) DO propagate. The redundant `AttributeError,` is a vestige of an earlier narrower catch (`except AttributeError`) that was widened during debugging and never cleaned up.
- **Intent violated:** n/a (correctness / dead-defensive code).
- **Suggested action:** delete the `AttributeError,` literal; the catch becomes `except Exception as exc:`. Verify the comment about BaseException propagation still matches.

## F-backtest-004
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/backtest/runner.py:149-186` (`_seed_initial_prices`).
- **Evidence:**
  ```
  for ticker in tickers:
      bars = store.read_ohlcv(ticker, window_start, window_end)
      prices[ticker] = float(bars[0].close) if bars else 0.0
  ```
  When a watchlist ticker has zero OHLCV bars in the window (e.g. the fetcher errored, the ticker was delisted, or the cache was never filled for it), `_seed_initial_prices` silently records `0.0` as the seed price. The FakeBroker then accepts BUY orders priced at zero, and the resulting "fills" pollute the entire downstream pipeline (zero-cost positions, infinite returns, divide-by-zero clamps). The docstring acknowledges this ("Tickers with no bar in the window keep ``0.0``") but does not raise or warn — exactly the silent-degradation pattern flagged in user feedback.
- **Intent violated:** test-policy §A.7 (surface silent failures); intent §2 silent-failure bias.
- **Suggested action:** raise a `RuntimeError(f"no OHLCV bars for {ticker} in window — cache fill incomplete?")` instead of defaulting to `0.0`. Fetcher gaps should fail the run at seed-time, not days of trades later.

## F-backtest-005
- **Category:** silent-failure / policy-violation
- **Severity:** P2
- **Location:** `src/backtest/decision_logger.py:339`.
- **Evidence:**
  ```
  "held_view_at_decision": _coerce(
      (state.get("positions") or {}).get(ticker)
  ),
  ```
  Reads the bare-key `state["positions"]` directly. Per intent §7.3, the bare-key `positions` is load-bearing only inside the executor's transactional zone; outside the executor, consumers must read `user:positions` (the persisted thesis book). `decision_logger.py:339` is invoked from the executor's post-execution hook, so the bare key happens to be populated at this moment — but the comment at lines 334-337 says "The strategist's context shim writes the structured book under `state['positions']`", which is the dropped/deprecated path (the context-shim writes are no longer the source of truth; the executor's `user:positions` writeback is). When the strategist context-shim is removed (a follow-on cleanup intent §7.3 anticipates), this read silently returns `None` for every decision row — `held_view_at_decision` always null.
- **Intent violated:** intent §7.3 (bare-key positions read outside executor); contract §C Rule 8 (observability additivity is fine, but the source-of-truth choice still matters).
- **Suggested action:** change the read to `(state.get("user:positions") or state.get("positions") or {}).get(ticker)` for now; once the strategist context-shim positions write is deleted, drop the `state.get("positions")` fallback.

## F-backtest-006
- **Category:** dead-test
- **Severity:** P2
- **Location:** `src/backtest/audit/telemetry.py:77-106` (`build_telemetry_record_from_logs`) and its only caller `tests/backtest/test_cache_hits_audit.py:37`.
- **Evidence:** `build_telemetry_record_from_logs` is referenced from exactly one test:
  ```
  $ grep -rn "build_telemetry_record_from_logs" src/ scripts/ tests/
  src/backtest/audit/telemetry.py:77  def build_telemetry_record_from_logs(...)
  tests/backtest/test_cache_hits_audit.py:37  from backtest.audit.telemetry import build_telemetry_record_from_logs
  tests/backtest/test_cache_hits_audit.py:39  record = build_telemetry_record_from_logs(log_payload=log_payload)
  ```
  No production code path uses it. The docstring describes it as the replacement for "the legacy `state['_report_cache_hits_for_audit']` surface" (the S3 fix), but the driver now uses `_drain_logs_cache_hits` (`driver.py:371, 423`) for the same job and assembles the telemetry record via `build_telemetry_record` (`driver.py:373`). `build_telemetry_record_from_logs` is the orphaned half of an in-progress migration.
- **Intent violated:** intent §1 (dead code); test-policy §A.7 ("tests must surface silent failures loudly" — a test pinning a function nothing else calls is anti-pattern §E).
- **Suggested action:** delete `build_telemetry_record_from_logs` and `tests/backtest/test_cache_hits_audit.py`. The cache-hit accounting is already covered by `_drain_logs_cache_hits` integration via the per-tick `*.tick.json` files.

## F-backtest-007
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/backtest/runner.py:632-647` (`_git_sha7`) and `:650-665` (`_git_sha_full`).
- **Evidence:** Two functions that differ only in the `--short=7` flag:
  ```
  def _git_sha7() -> str:
      ... ["git", "rev-parse", "--short=7", "HEAD"] ...
      except Exception: return "unknown"

  def _git_sha_full() -> str:
      ... ["git", "rev-parse", "HEAD"] ...
      except Exception: return "unknown"
  ```
  Called from one place each (`_git_sha7` at line 374, `_git_sha_full` at line 485).
- **Intent violated:** intent §1 (dedupe).
- **Suggested action:** collapse into one `_git_sha(short: bool = False)` helper, or inline both at the (single) call sites. Low priority — the duplication is small and clearly named.

## F-backtest-008
- **Category:** over-abstraction
- **Severity:** P2
- **Location:** `src/backtest/reporting.py:412-415`, `:502-526` (N/A-by-string signalling pattern).
- **Evidence:** `_spy_benchmark_series` and friends return `float | str` where the string is a human-readable "N/A — SPY not in cache" message and the float is the real value. Every downstream consumer then does `isinstance(x, str)` to branch:
  ```
  spy_sharpe = spy_series if isinstance(spy_series, str) else "N/A — SPY series too short"   # line 150
  matched_sharpe = matched_series if isinstance(matched_series, str) else "N/A …"            # line 166
  avg_exposure_str = "_N/A_"                                                                 # line 523
  win_rate_str = … if not (isinstance(win_rate, float) and win_rate != win_rate) else "…"    # line 526 (NaN check via x != x)
  ```
  This conflates two distinct concerns (value-or-absent vs render-string) in one union type. The `isinstance(x, str)` checks are scattered (lines 150, 166, 167, 176, 502, 508, 523, 526), and the NaN check on line 526 (`win_rate != win_rate`) is even more obscure.
- **Intent violated:** intent §1 (over-abstraction — a tagged-union return type doing two jobs); intent §2 (silent-failure bias — the string-as-error-channel is exactly the "neutral on absence" pattern user feedback flags).
- **Suggested action:** investigate — separate the data type (Optional[float] or a dedicated `Metric` Pydantic model with `value` + `reason_missing`) from the render layer. Reporting becomes "format a Metric"; absence reasons live as enum / string attribute. ~50 lines of `isinstance` checks would disappear.

## F-backtest-009
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/backtest/cache/store.py:867` (`_audit_capture_enabled`).
- **Evidence:** `_audit_capture_enabled` is a method that returns `getattr(self, "_audit_reads", None) is not None`. Called only by `_audit_record` (line 883) which is itself called from each `read_*` method. A single-caller no-arg predicate is over-abstraction; inline the check at the one site.
- **Intent violated:** intent §1 (over-abstraction).
- **Suggested action:** inline `_audit_capture_enabled` into `_audit_record`. Combined with F-backtest-001 this whole inline mechanism may disappear anyway.

## F-backtest-010
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/backtest/audit/upstream_verifier.py:159-217` (`_verify_filing`, `_verify_news`).
- **Evidence:** Both verifier hooks are documented as "real implementation hits sec.gov" / "wire up Tiingo HTTP re-fetch when the first audit run surfaces a need" — i.e. the bodies are placeholders that return `{"source": …, "agreement_with_cache": True}` without ever calling upstream. The placeholders set `agreement_with_cache: True` unconditionally, so the deep-dump's `upstream_disagreement` counter (`deep_dump.py:124`) can never fire in practice. The summary line in `_build_summary` will always say "✅ 0 rows: cached value disagreed with upstream re-fetch by >60s" — a tripwire that cannot fire is not a tripwire.
- **Intent violated:** intent §2 (silent-failure bias — a check that always passes is worse than no check, because reviewers trust the green tick).
- **Suggested action:** investigate — either implement the upstream re-fetches (per the spec these were always promised) or change the placeholders to return `"agreement_with_cache": None` (unknown) and have `_build_summary` render "⚪ <n> rows: upstream not verified". Don't ship a permanently-green tripwire.

## F-backtest-011
- **Category:** dead-code (provider registered for a domain whose data is never fetched)
- **Severity:** P3
- **Location:** `src/backtest/providers/politician_trades_cache.py:21` (registered) vs `scripts/backtest_fetch.py:327` (provider intentionally disabled).
- **Evidence:** `politician_trades_cache` registers a "cache" provider for the politician_trades domain at module-import, but `scripts/backtest_fetch.py:318-327` explicitly comments out the politician_trades fetch ("no free historical source; the smart_money analyst already degrades gracefully"). Per user memory `project_politician_trades_disabled`, this is intentional — the provider stays registered so the registry has a fallback shape, and the analyst handles the empty case. No bug, just non-obvious.
- **Intent violated:** n/a — matches user-confirmed policy (`feedback_provider_switching_must_be_one_line`: keep "shell" providers registered).
- **Suggested action:** no removal. Flagging because it shows up as "dead-looking" in a structural scan; per user memory it's load-bearing-by-policy.

## F-backtest-012
- **Category:** dead-code (commented-out fetch path)
- **Severity:** P3
- **Location:** `scripts/backtest_fetch.py:329-341` (notable_holders disabled).
- **Evidence:** `notable_holders` is commented out in `_build_provider_fns` (2026-05-19 note explaining edgartools issuer-vs-subject mismatch). The `_notable_holders` provider function (`scripts/backtest_fetch.py:294-310`) is therefore unreferenced — dead until the underlying provider problem is fixed. Same posture as politician_trades but newer.
- **Intent violated:** n/a — documented temp disablement.
- **Suggested action:** no removal — keep as scaffolding per the same policy as F-backtest-011, but ensure the notable_holders cache provider is also still registered so the analyst sees an empty list (verify analogous to politician_trades — `tests/backtest/leak_regressions/test_politician_same_day_disclosure_not_visible.py` covers the equivalent for politicians).

## F-backtest-013
- **Category:** test-gap
- **Severity:** P2
- **Location:** `tests/integration/backtest/test_end_to_end_smoke.py` (756 LoC — the largest single test file).
- **Evidence:** One mega-file holds the end-to-end smoke. This is the canonical baseline-2025-09 + tick_limit=1 path (test-policy hard-rule compliant), but at 756 lines it mixes setup, assertion, fixture wiring, and orchestration. The test-policy taxonomy is "one concern per file" and this file violates it. The cost is that when the smoke breaks, bisecting which assertion fired requires reading hundreds of lines of inline scaffolding.
- **Intent violated:** test-policy taxonomy / layout principle.
- **Suggested action:** investigate — split into per-concern smokes (`test_smoke_pipeline_completes.py`, `test_smoke_telemetry_written.py`, `test_smoke_decision_logger_writes.py`, etc.) sharing a conftest fixture. Defer if the file is stable; the size alone is not a bug.

## F-backtest-014
- **Category:** layout
- **Severity:** P3
- **Location:** `src/backtest/audit/__init__.py` (11 lines, docstring only).
- **Evidence:** The audit subpackage `__init__.py` is a docstring banner with no re-exports. Imports go directly to submodules (`from backtest.audit.telemetry import …`, `from backtest.audit.deep_dump import …`). No bug; matches the "no over-abstraction in init" preference.
- **Intent violated:** n/a.
- **Suggested action:** none. Flagging only for completeness.

## F-backtest-015
- **Category:** policy-mismatch (doc-only)
- **Severity:** P3
- **Location:** `src/backtest/runner.py:531` comment.
- **Evidence:** Comment block in runner state seed says "the runner re-hydrates `user:positions` from the DatabaseSessionService row on tick 2+; Band 4 will wire the Executor writer-of-record to persist it there." The "Band 4" reference is pre-completion language; Band 4 has landed (per intent §4 lifecycle). The comment is stale and could mislead a future reader into thinking the executor writeback is still a TODO.
- **Intent violated:** n/a.
- **Suggested action:** update the comment to reflect the current state — executor writeback IS the source of truth for `user:positions`; runner seed is intentionally empty so the DB row wins on tick 2+.

---

## Cross-cutting summary

- **P0 (0):** None — no production-down bugs surfaced.
- **P1 silent-failures + over-abstraction (4):** F-backtest-001 (dual cache-capture mechanisms), F-backtest-002 (`except RuntimeError: pass` quartet), F-backtest-003 (`(AttributeError, Exception)` redundant tuple), F-backtest-004 (`_seed_initial_prices` 0.0 default).
- **P2 (6):** F-backtest-005 (decision_logger bare-key positions), F-backtest-006 (`build_telemetry_record_from_logs` dead path), F-backtest-007 (`_git_sha*` duplicate helpers), F-backtest-008 (N/A-by-string signalling in reporting), F-backtest-009 (`_audit_capture_enabled` single-caller), F-backtest-010 (upstream verifiers always-green), F-backtest-013 (`test_end_to_end_smoke.py` mega-file).
- **P3 (3):** F-backtest-011 (politician_trades shell — keep), F-backtest-012 (notable_holders shell — keep), F-backtest-014 (audit `__init__.py` is doc-only), F-backtest-015 (stale "Band 4" comment).

**Top three for human attention:**
1. **F-backtest-004** — `_seed_initial_prices` silently maps absent tickers to `0.0`. A fetcher gap (or a typo in `watchlist.json`) becomes a corrupted run that never fails loudly. Trivial to raise instead; exact silent-degradation class the user flags as recurring.
2. **F-backtest-001** — two parallel cache-row-capture mechanisms (inline `_audit_*` on `CachedDataStore` for Layer 1 telemetry; `AuditingStore` decorator for Layer 2 deep-dump). Same shape, same call sites, same data, two implementations. Pick one. Decorator is structurally cleaner; inline has lower overhead.
3. **F-backtest-010** — `upstream_verifier._verify_filing` and `_verify_news` are documented as "wire up later" placeholders that hard-code `agreement_with_cache=True`. The `upstream_disagreement` tripwire is therefore impossible to fire — reviewers reading "✅ 0 rows: cached value disagreed with upstream" will trust a guarantee that does not exist. Either implement the verifiers or change the placeholder to "unknown" so the SUMMARY.md renders neutrally rather than green.
