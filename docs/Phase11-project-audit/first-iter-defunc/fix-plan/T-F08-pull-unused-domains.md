# T-F08 — Pull unused data domains

**Wave:** 2 (parallel — runs after T-F10 layout sweep merges)
**Pairs source-audit fix:** F6 (pull `earnings`, `analyst_consensus`,
`short_interest`, `options`)
**Branch:** `fix/T-F08-pull-unused-domains`
**Depends on:** T-F10 (layout sweep) — provider test paths have been
collapsed into `tests/unit/data/providers/` before this PR runs.
**Estimated diff size:** medium (deletions) / nothing added

## Scope

Pull the four wired-but-unused Phase 3 data domains end-to-end, per the
strategic decision in `docs/Phase11-project-audit/fix-plan/README.md` Decision 2. Per
`data-models-and-top-level.md` P1-02 and the user memory
`feedback_provider_switching_must_be_one_line`, the four domains
(`earnings`, `analyst_consensus`, `short_interest`, `options`) pass the
import-time registration validator and appear in `DOMAIN_SHAPES`, but
nothing in `src/` outside `tests/contract/test_provider_shapes.py`
dispatches any of them. There is no `get_*` wrapper in
`src/data/__init__.py` for any of the four; there is no analyst that
consumes their Pydantic outputs. The wiring is a maintenance debt and a
contract-test branch carrier with no live consumer. When a real consumer
arrives the domain can be reinstated; until then the surface stays
hidden so contributors don't mistake it for load-bearing.

Source + tests ship together per README.md Decision 3.

### In scope

**1. Delete the four Pydantic model modules.**

- `src/data/models/earnings.py`
- `src/data/models/analyst_consensus.py`
- `src/data/models/short_interest.py`
- `src/data/models/options.py`

Patch `src/data/models/__init__.py`: remove the imports for
`EarningsHistory`, `AnalystConsensusBundle`, `ShortInterestSnapshot`,
`OptionContract` and the corresponding `__all__` entries.

**2. Delete the four provider modules and their subpackages.**

- `src/data/providers/earnings/finnhub.py`
- `src/data/providers/earnings/__init__.py`
- `src/data/providers/earnings/` (directory)
- `src/data/providers/analyst_consensus/yfinance.py`
- `src/data/providers/analyst_consensus/__init__.py`
- `src/data/providers/analyst_consensus/` (directory)
- `src/data/providers/short_interest/finra.py`
- `src/data/providers/short_interest/__init__.py`
- `src/data/providers/short_interest/` (directory)
- `src/data/providers/options/yfinance.py`
- `src/data/providers/options/__init__.py`
- `src/data/providers/options/` (directory)

If `src/data/providers/__init__.py` carries explicit imports for any of
these (verify per `data-providers.md` P2-01 — provider-registration
strategy is currently split between parent and subpackage init files),
remove those imports too.

**3. Remove the four domains from the registry and config.**

- `src/data/registry.py` — delete the four `DOMAIN_SHAPES` rows at
  lines 94-97 (`earnings`, `analyst_consensus`, `short_interest`,
  `options`) and the four `DOMAINS` entries at lines 112-115. Also
  remove the four imports at lines 15, 17, 20, 27.
- `src/data/config.py` — delete the four entries in `_DOMAINS` at
  lines 27-30 (kept in sync with `registry.DOMAINS` per source-audit
  `data-models-and-top-level.md` P3-03).
- `config/data.json` — delete the four provider lines for
  `earnings`, `analyst_consensus`, `short_interest`, `options` from
  the `providers` block.

**4. Update `config/README.md`.**

Delete the rows / paragraphs describing the four removed domains'
provider settings. If `config/README.md` carries a "future analyst"
note for any of these, replace with a single line under "Removed
domains" or similar: "earnings / analyst_consensus / short_interest /
options were pulled on 2026-05-25 because no consumer existed; see
`docs/Phase11-project-audit/fix-plan/T-F08-pull-unused-domains.md` for context. Reinstate
when an analyst is added."

**5. Delete the model round-trip and provider unit tests.**

Per `data-models-and-top-level.md` P1-01 and `data-providers.md` P2-04.
Post-T-F10 paths:

Model tests:
- `tests/unit/data/models/test_earnings.py` (5 tests)
- `tests/unit/data/models/test_analyst_consensus.py` (6 tests)
- `tests/unit/data/models/test_short_interest.py` (4 tests)

(There is no `tests/unit/data/models/test_options.py` per
`data-models-and-top-level.md` P2-04 — the asymmetric gap. No deletion
needed there; no new test added.)

Provider tests:
- `tests/unit/data/providers/test_earnings_finnhub_as_of.py`
- `tests/unit/data/providers/test_analyst_consensus_yfinance.py`
- `tests/unit/data/providers/test_short_interest_finra_as_of.py`
- `tests/unit/data/providers/test_options_yfinance_shell.py`

**6. Surgically patch the contract shape test.**

`tests/contract/test_provider_shapes.py` is the only surviving caller
of the four domain dispatch paths (`data-models-and-top-level.md`
P1-02). Remove:
- The four `if domain == "earnings"` / `"analyst_consensus"` /
  `"short_interest"` / `"options"` branches around lines 286-356 (the
  audit-cited range — verify by inspection).
- The four entries from the `_LIVE_ONLY` set at line 548.

Do **not** delete the whole file — `test_provider_shapes.py` is the
load-bearing layer-boundary test for the other 8 domains and remains
valid.

**7. Delete the legacy `quiver` politician-trades anchor test.**

Per `data-models-and-top-level.md` P1-04 / P1-05 (the politician-trades
test is an anchor on a path Quiver no longer holds; the
`project_politician_trades_disabled` memory documents that the domain
is intentionally inactive). The test in question:

- `tests/unit/data/test_provider_switching.py::test_politician_trades_swap_fmp_to_quiver`
  (lines 100-138).

Delete only that test (not the other tests in the file — the news
provider swap tests stay).

This deletion is included here, not in T-F07, because it pairs with
the data-domain pull theme rather than the SmartMoney deletion.

**8. Update `src/data/__init__.py` rate-limit table.**

Per `data-models-and-top-level.md` P1-03 (Quiver references stale):
- Refresh the docstring table at lines 13-32 to mirror the *current*
  `config/data.json` provider set after the four-domain pull.
- Remove the Quiver references; the active politician-trades
  provider is FMP.
- Re-derive the `min_decision_interval_seconds()` floor sentence from
  the actual function output (the audit notes the narrative "Quiver
  2s floor" no longer applies).

**9. Touch up any leftover doc references.**

- `docs/contract-invariants.md` — `grep` for the four domain names
  and the four model class names; remove any active reference.
- `docs/data-and-providers.md` (or `data-sources.md` if it's still
  canonical) — same treatment.

### Out of scope

- Adding `get_earnings(...)` / `get_analyst_consensus(...)` etc.
  wrappers. The strategic decision is "pull, reinstate when a
  consumer arrives" — the wrappers stay deleted.
- The four `15.0`-second hardcoded HTTP timeout findings in
  `data-providers.md` P3-01 to P3-05 — three of those providers
  are deleted by this PR (earnings/finnhub, news/tiingo,
  news/alpha_vantage, short_interest/finra, politician_trades/fmp);
  but news/tiingo and news/alpha_vantage and politician_trades/fmp
  survive this PR (news and politician_trades stay). Their timeout
  config violations are deferred to a separate small cleanup ride
  later.
- `data-models-and-top-level.md` P1-04 (`is_missing_timestamp(None)`
  attractor) — separate fix; pairs with source F4 surfacing primitive
  in Wave 4.
- The dual `PositionThesis` cleanup — owned by T-F05.
- `data-providers.md` P0-01 to P0-03 EDGAR + social-403 surfacing
  fixes — owned by Wave 4 T-F01b (paired with source F4 surfacing
  primitive).
- SmartMoney deletion — owned by **T-F07**, which runs in parallel
  in Wave 2.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `data-models-and-top-level.md` (source) P1-02 | four Phase 3 domains | Pull end-to-end |
| `data-models-and-top-level.md` (source) P1-03 | `data/__init__.py:13-32` | Refresh rate-limit table after pull |
| `data-models-and-top-level.md` (test) P1-01 | three model test files | Delete |
| `data-models-and-top-level.md` (test) P1-02 | `test_provider_shapes.py` four branches | Surgically remove |
| `data-models-and-top-level.md` (test) P1-04 | `test_provider_switching.py::test_politician_trades_swap_fmp_to_quiver` | Delete legacy anchor |
| `data-models-and-top-level.md` (test) P1-05 | (same as P1-04 — Quiver anchor) | Delete |
| `data-models-and-top-level.md` (test) P2-04 | missing `test_options.py` | Moot — options is pulled |
| `data-providers.md` (test) P2-04 | four conditional T1 provider tests | Delete |
| `data-providers.md` (source) P2-03 | `options/yfinance.py` `symbol` vs `ticker` | Closed by deletion |

## Implementation steps

1. **Pre-flight grep audit.** Run
   `grep -rn "earnings\|analyst_consensus\|short_interest\|options"
   src/data/ src/contract/ src/orchestrator/ tests/ config/
   docs/contract-invariants.md`. Filter to the four target domains
   (the words `options`/`earnings`/`short` appear elsewhere in unrelated
   contexts — `--word-regexp` and per-key checks help). Save as the
   work-tracking source.
2. **Delete model modules:**
   - `rm src/data/models/{earnings,analyst_consensus,short_interest,options}.py`
   - Edit `src/data/models/__init__.py`: drop imports + `__all__`
     entries for `EarningsHistory`, `AnalystConsensusBundle`,
     `ShortInterestSnapshot`, `OptionContract`.
3. **Delete provider modules:**
   - `rm -rf src/data/providers/{earnings,analyst_consensus,short_interest,options}/`
   - Edit `src/data/providers/__init__.py`: drop any explicit imports of
     the deleted modules.
4. **Patch the registry:**
   - Edit `src/data/registry.py`: remove imports at lines 15, 17, 20,
     27; remove `DOMAIN_SHAPES` rows at lines 94-97; remove `DOMAINS`
     entries at lines 112-115.
5. **Patch the config loader:**
   - Edit `src/data/config.py`: drop the four entries in `_DOMAINS`
     at lines 27-30.
6. **Patch the config file:**
   - Edit `config/data.json`: delete the four provider lines
     (`earnings`, `analyst_consensus`, `short_interest`, `options`).
7. **Refresh `config/README.md`:** remove the four domains'
   documentation. Add a one-line "removed on 2026-05-25; see
   `docs/Phase11-project-audit/fix-plan/T-F08-pull-unused-domains.md`" note if the README
   format supports it.
8. **Delete tests** (against post-T-F10 paths):
   - `rm tests/unit/data/models/{test_earnings,test_analyst_consensus,test_short_interest}.py`
   - `rm tests/unit/data/providers/{test_earnings_finnhub_as_of,test_analyst_consensus_yfinance,test_short_interest_finra_as_of,test_options_yfinance_shell}.py`
9. **Surgically patch the contract shape test:**
   - Edit `tests/contract/test_provider_shapes.py`: remove the four
     `if domain == "..."` branches around lines 286-356; remove the
     four entries from `_LIVE_ONLY` at line 548.
10. **Delete the Quiver anchor test:**
    - Edit `tests/unit/data/test_provider_switching.py`: delete the
      `test_politician_trades_swap_fmp_to_quiver` function (lines
      100-138).
11. **Refresh the `data/__init__.py` docstring table:**
    - Edit `src/data/__init__.py`: rewrite the rate-limit table at
      lines 13-32 to mirror `config/data.json` post-deletion; remove
      Quiver references; re-derive the floor sentence.
12. **Touch up `docs/contract-invariants.md` and
    `docs/data-and-providers.md`:** remove any active references to
    the four pulled domains.
13. **Post-flight grep audit.** Re-run step 1's grep. The only
    acceptable surviving matches are:
    - Past-tense historical commentary in `docs/`.
    - The literal words `option` / `earnings` in unrelated semantic
      contexts (e.g. `options=` Python kwargs, `earnings` in a comment
      about share-price drivers — verify each surviving hit by hand).
14. **Run the full suite** and `ruff check`.
15. **Run a single-tick backtest** to confirm the orchestrator still
    wires correctly:
    `PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window
    baseline-2025-09 --tick-limit 1`.
16. Append a `graphify-out/graph_delta.md` entry.

## Acceptance criteria

- [ ] `grep -rn "EarningsHistory\|AnalystConsensusBundle\|ShortInterestSnapshot\|OptionContract" src/ tests/`
  returns no matches.
- [ ] `src/data/models/{earnings,analyst_consensus,short_interest,options}.py`
  no longer exist; the four provider subpackage dirs no longer exist.
- [ ] `python -c "from data import registry; assert 'earnings' not in
  registry.DOMAINS and 'options' not in registry.DOMAINS"`
  succeeds (run via `.venv/bin/python -m`).
- [ ] `tests/contract/test_provider_shapes.py` still exists and still
  collects tests for the other 8 domains.
- [ ] Full `pytest tests/ -v` green.
- [ ] `.venv/bin/python -m ruff check src/` clean.
- [ ] `PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window
  baseline-2025-09 --tick-limit 1` runs to completion.
- [ ] Every finding in the table above is closed (cite by ID in the
  commit body).
- [ ] `graphify-out/graph_delta.md` has an entry dated today.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
grep -rn "EarningsHistory\|AnalystConsensusBundle\|ShortInterestSnapshot\|OptionContract" src/ tests/
```

## Risks and rollbacks

- **Risk:** the import-time validator
  `_validate_active_providers_are_registered` in `src/data/__init__.py`
  fails because `config/data.json` references a domain not in
  `registry.DOMAINS`. Mitigation: edit `config/data.json` (step 6)
  before reloading `data` in any verification command; the post-flight
  grep step catches lingering provider rows.
- **Risk:** an `__init__.py` `__all__` typo leaves a stale name
  imported. Mitigation: `ruff check` catches unused imports; the full
  suite catches broken imports.
- **Risk:** `data/__init__.py`'s `min_decision_interval_seconds()` was
  computed from the old `DOMAINS` list and a refresh changes its
  output. Mitigation: re-run any test that pins that function's value
  (`grep -rn min_decision_interval_seconds tests/`) and update the
  expected value if pinned. Today the function's behaviour is driven by
  the active provider set, not the registered set, so the deletion
  should be transparent — but verify.
- **Risk:** the `test_provider_shapes.py` surgical edit accidentally
  drops a non-target domain. Mitigation: inspect the diff for the
  file by hand before commit.
- **Rollback:** discard the feature branch.

## Subagent dispatch prompt sketch

> Work on branch `fix/T-F08-pull-unused-domains` in a git worktree.
> Depends on T-F10 having merged first — confirm. Read
> `docs/Phase11-project-audit/fix-plan/T-F08-pull-unused-domains.md` end-to-end, then read
> `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md`,
> `docs/Phase11-project-audit/source-audit/data-providers.md`,
> `docs/Phase11-project-audit/test-audit/data-models-and-top-level.md`, and
> `docs/Phase11-project-audit/test-audit/data-providers.md` for context. Delete the four
> domains' models, providers, registry rows, config rows, tests, and
> contract-test branches as listed. Refresh `config/README.md` and
> `src/data/__init__.py`'s rate-limit docstring. Confirm zero surviving
> active references via pre/post-flight grep. Run the full test suite,
> `ruff check`, and a single-tick backtest. Commit as
> `fix(data): pull unused earnings / analyst_consensus / short_interest
> / options domains` with finding IDs in the body. Push and open the PR.
> **Do not skip hooks. Do not amend. Do not add back the deleted
> domains under any circumstances — reinstate only when a real consumer
> exists.**
