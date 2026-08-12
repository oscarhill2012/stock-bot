# Plan 3d — Thesis Split & Derived Commitments

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking. Follow each task in order. Every task is a
> self-contained TDD cycle: write the failing test, watch it fail, implement, watch it
> pass, commit. Do not skip the failing-test step. Do not batch commits across tasks. If a
> step's observed behaviour diverges from what the plan predicts, STOP and re-read the
> source file before improvising.

**Spec:** `docs/Phase14-analyst-refactor/specs/thesis-split-and-commitments.md` — every
decision in it is settled (marked *Decision 2026-08-12*). Do not re-litigate a decision
recorded there; if implementation makes one look wrong, STOP and raise it.

**Sequencing:** Lands *after* Plan 3c (analyst lean recalibration), whose anchored horizons
and digest aggregate this plan consumes. It is numbered `3d` for lineage — it governs how
long a position is *held*, which is the layer immediately above the analyst leans Plan 3c
recalibrated, without renumbering Plans 4–5. Plans 4 and 5 remain sequenced after.

**Goal:** Give the strategist a clock on its own positions. The `first-month-6` run
converted a real analyst edge into a −$1,196 realised loss across 9 round-trips with a
median hold of 6 sessions against 20/60/90-day signals, because nothing in the prompt
records what a position was bought on. Split the thesis book so held positions and unowned
views stop competing for the same budget (63% of rendered rows were `[NO POSITION]`, 37% of
the whole book bearish theses on unshortable tickers), and attach to every position a
deterministically-derived commitment ledger that ages in calendar days and cannot be
authored, extended or cleared by the LLM.

**Architecture:** Two preconditions, then six surfaces, landed in dependency order.
Tasks 1–2 are the spec's preconditions: the position store is made to follow the *fill*
rather than the *stance* (a float-rounding sell rejection left DOV held at 4.6% of NAV and
invisible to the strategist for 21 ticks), and the prompt surface is cleaned before it is
grown. Then: (3) config; (4) schema work — `Commitment` and `PositionEvent` value objects,
a new `WatchNote`, `PositionThesis` entry fields promoted to required, and the two
prose-staleness fields deleted; (5) a pure `derive_commitments` implementing the
create / refresh / contradict rules against the three canonical `*_verdicts` batches;
(6) `_verb_dispatch` rewritten around two stores instead of one nullable discriminator,
plus material-trim events; (7) executor wiring; (8) render; (9) the new prompt content.

**Tech Stack:** Python 3.14, Pydantic v2, Google ADK, SQLAlchemy/SQLite, pytest.

## Global Constraints

Every task's requirements implicitly include this section.

- **British English everywhere** — code identifiers, comments, docs, prose (`behaviour`,
  `normalise`, `analyse`, `colour`, `optimise`).
- **Comment-heavy code** — every function gets a docstring (purpose, parameters, return
  value); non-trivial logic gets inline comments; blank lines separate logical blocks.
- **Config convention** — every tunable lives in `config/*.json`; each addition/removal
  updates `config/README.md` in the *same* task. Never hardcode a config value in source.
- **High-value tuning-knob markers** — `material_trim_fraction` is a tuning knob and its
  read site MUST carry a comment reading `# HIGH-VALUE TUNING KNOB:` explaining the
  direction of the trade-off, so a later tuner finds it by grep.
- **Loud failures** — prefer raises over silent null/empty/neutral degradation. This plan
  exists partly because a correct extractor silently produced nothing in 600/600 real rows
  while its unit tests passed, and partly because a rejected broker order silently deleted
  a thesis row for a position that stayed open. Tests here assert positive signals (a
  commitment was written, with this analyst and this lean), never merely the absence of an
  error.
- **`as_of` / PIT discipline** — every read of `state["as_of"]` goes through
  `resolve_as_of`; every datetime written to ADK state is ISO-stringified first (the
  backtest `DatabaseSessionService` cannot hold `datetime`). `Commitment.anchored_at`,
  `PositionEvent.at` and `WatchNote.last_reviewed_at` all follow this.
- **Horizons are CALENDAR days** — everywhere. `forward_return_horizons_days`, the
  fundamental filing decay, the news carry's `day N/H` render and this plan's commitments
  all use the same unit. Do not introduce a trading-session counter; there is no
  `sessions_between` helper and none should be written.
- **No orphaned artefacts** — the spec's Removals section names exactly what dies. Delete
  it in the task that supersedes it: no dead functions, stale config, commented-out
  blocks, or half-migrated call sites left behind.
- **Shell conventions** — never prefix Bash commands with `cd`; the tool already runs in
  the project root. Tests: `.venv/bin/python -m pytest <path> -v`. Scoped scripts:
  `PYTHONPATH=src .venv/bin/python -m scripts.<name>`. Lint:
  `.venv/bin/python -m ruff check src/`.
- **One commit per task** — each task ends with a single commit; do not batch across tasks.

## Cross-plan assumptions (verify once, do not re-litigate)

- Plan 3c has landed: `technical_verdicts` / `news_verdicts` / `fundamental_verdicts` are
  published as canonical (non-`temp:`) state keys by their joiners, each verdict carries
  `horizon_days` injected from config via `LlmTickerVerdict.to_ticker_verdict`, and
  abstains are flagged rather than counted as neutral votes. This plan reads those
  surfaces; it does not create them. If any is absent, STOP.
- The golden cache stores **provider data only**. Thesis rows and commitments are generated
  live each run and never replayed from cache, so this schema change cannot invalidate
  cached data.
- The project is **pre-deployment** — no paper or live instance exists, so
  `state["user:positions"]` has no production data behind it. This is a breaking schema
  change with no migration path, and none should be invented.

---

## Task 1 — Precondition P-A: the book follows the fill, not the stance

**Files:** `src/agents/executor/agent.py`, `src/broker/` (the sell path),
`tests/integration/test_executor_with_fake_broker.py`,
`tests/unit/agents/executor/test_position_reconciliation.py` (new)

This is spec precondition P-A. The observed failure, from
`backtests/long-baseline-2025/runs/first-month-6`:

```
2025-09-05  DOV buy    → position opened
2025-09-22  DOV sell   "Closing position as the extremely weak technicals…"
            broker: rejected —
            'sell 28.529340021135052 > held 28.52934002113505 of DOV'
2025-09-23  DOV update "Initiating a neutral thesis… wait-and-see approach"
2025-09-24 → 10-13  no_action × 21
```

`trade_log` holds no DOV row. The position stayed open at ~4.6% of NAV for the rest of the
run while the thesis row was deleted on the strategist's *intent*.

- [ ] Write a failing test that reproduces the rejection exactly: hold a quantity, request
      a full close whose computed quantity exceeds it by ~1e-15, assert the order is
      accepted and the position reaches zero. Use the real numbers above so the test names
      the bug.
- [ ] Fix the sell path to clamp the outgoing quantity to the broker's reported holding.
      This is arithmetic, not a tolerance: `min(requested, held)`. Do NOT introduce an
      epsilon comparison — an epsilon large enough to absorb this is large enough to hide a
      genuine over-sell.
- [ ] Write a failing test asserting that a **rejected** full-close leaves the thesis row
      in place. Row deletion must be conditional on the close having executed.
- [ ] Implement that conditionality in the executor's post-execution callback. The
      `HALLUCINATED` sentinel and its `"hallucinated_stance"` log key are unrelated and
      unchanged.
- [ ] Write a failing test for the reconciliation raise: seed a broker holding with no row
      in `user:positions`, run a tick, assert it raises with **both sides named** (ticker,
      broker quantity, store state). Then the mirror case — a row with no holding.
- [ ] Implement the per-tick reconciliation. It runs after the position store is written.
      Per the loud-failure convention this raises; it does not warn and continue.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- Under the current single-book model a divergence degrades to a `[NO POSITION]` row that
  at least still renders, which is how DOV survived 21 ticks unnoticed. After Task 6's
  split there is no landing zone at all — the ticker vanishes from `user:positions`, carries
  no watch note, and its commitments are destroyed while the position lives on. **This task
  is what makes the split safe and must land before it.**
- Re-run the first month after the fix and confirm DOV either closes on 2025-09-22 or
  retains its thesis row. Record which, in the commit message.

---

## Task 2 — Precondition P-B: prompt-surface hygiene sweep

**Files:** `src/agents/strategist/prompts.py`, `src/contract/strategist_prompt.py`,
`tests/unit/contract/test_strategist_prompt_layout.py`

This is spec precondition P-B. Clean the surface before growing it. Everything below is a
rendered-to-the-model defect, not a code comment.

- [ ] **Measure first.** Render a full prompt from the `first-month-6` cache and record
      line count and byte size. Baseline: **1,581 lines / 115KB**, evidence block lines
      110–1332 (77%). Put the before/after numbers in the commit message.
- [ ] Write failing tests for each contradiction, then fix:
      - `prompts.py:264` and `:270` instruct trimming via `update` (smaller weight);
        `:362` and `:368` state `update` takes no weight and the schema rejects it. Rewrite
        both deployment bullets to use `sell` for trims. **This is the highest-value item
        in the task** — the prompt currently asks for a stance that aborts the tick.
      - `:396` conditions the output mix on `first_tick_flag`; the placeholder is
        deliberately absent from the template (`context_shim.py:222`). Remove the
        reference — `## Mode` and `{temp:first_tick_preamble}` already carry the framing.
        Three sites name it: the module docstring (`:27`, which describes it as "a runtime
        ADK slot" it is not), `FIRST_TICK_PREAMBLE` (`:125`), and `:396`. All three go —
        grep `first_tick_flag` afterwards and expect zero hits in this file.
      - `:177` frames the decision as "for the next trading hour" against 20/60/90-day
        signals and a 1–2 tick/day schedule. Replace with wording that does not assert a
        horizon at all — the horizon guidance belongs to the analyst horizon section.
      - `:253` instructs the model to sum the `current weight` lines manually, directly
        beneath `{temp:deployment_readout}` which already states the figure. Delete the
        manual-sum instruction and keep the readout as the single source.
      - the cash-floor stanza renders "full deployment is permitted when conviction
        supports it" (`:91-93`, because `cash_floor_weight` is 0.00) beside "Sum > 0.95 —
        over-deployed. Trim." Reconcile: the stanza should state that no *hard* floor
        applies while the 70–95% band still governs posture.
- [ ] Delete the dated prose, no replacement needed:
      - `:186` heading — "with evolution since the last revision". Nothing evolutionary is
        rendered. (Task 8 revises this heading again for the split; do the honest version
        now rather than leaving a known-false claim standing for six tasks.)
      - `:209` — "The technical analyst **now** gives you three INDEPENDENT reads". Drop
        "now"; the model has no memory of the previous version.
      - `:204-207` — a maintenance note addressed to a human developer ("if those config
        values ever change, update this prose to match"). Delete it outright. The hardcoded
        60/90/20 in the surrounding prose duplicate config; rewrite the paragraph to speak
        about horizons generically and let the per-verdict `horizon:` lines carry the
        numbers.
      - `:371-373` — forbids `target_price` / `stop_price` / `horizon` by first teaching
        all three. Reduce to the general rule already stated: `rationale` is the only prose
        field, and fields outside the schema are rejected.
      - `:213-218` — the technical-lean paragraph, which calls the horizon
        "~60-**trading**-day" (wrong unit — see Global Constraints) and describes the dead
        `anchor:` line. Fix the unit and delete the `anchor:` sentence. The clause itself
        dies in Task 9.
- [ ] Write a failing test asserting no rendered feature line has `0.0` as its only value,
      then suppress zero-valued insider flags in `strategist_prompt.py`. Baseline: **190
      such lines, 12% of the evidence block** — `Cluster buy flag` ×20,
      `Conviction buy flag` ×20, `Conviction sell flag` ×20, `Derivative exercises` ×20,
      `Derivative grants` ×20, `Cluster sell flag` ×18, `Planned sale ratio` ×16, and
      others. The `-> Closed-vocab tags:` line already carries anything that fired, so
      suppression loses no information.
- [ ] Collapse the `_HORIZON_PROSE` duplication at `strategist_prompt.py:543-551` — each
      line currently states its horizon twice (`horizon: ~60d — … decays within ~60 days`),
      and fundamental states it three times (`~90d`, `~90 days`, `~3 months`). Keep the
      numeral once.
- [ ] Collapse news-carry triple-statement: `-> Closed-vocab tags: carried`, the rationale
      prose `"carried catalyst from … (day 12/20, decayed)"`, and `[news carried]` on the
      aggregate line all say the same thing. Keep the rationale prose — it carries the
      anchor date and day count, which the other two do not — and drop the bare `carried`
      tag and the aggregate suffix.
- [ ] **Reorder.** Move `## Your Job`, `## Reading analyst reports` and
      `## Reading the technical reads and analyst horizons` **above**
      `## Ticker Evidence`. Task framing precedes data. Add a layout test asserting the
      `## Your Job` heading appears before the `## Ticker Evidence` heading.
- [ ] Re-measure. Run tests, watch them pass. Lint. Commit.

**Requirements**
- Do NOT touch the Thesis Book render in this task — that is Task 8. Only the template
  prose and the evidence renderer are in scope here.
- The ordering change and zero-suppression alone should take the prompt below 1,000 lines.
  If it does not, report the actual number rather than cutting further to hit a target.

---

## Task 3 — Config keys for trims, watch notes and event rendering

**Files:** `config/strategist.json`, `config/README.md`,
`src/config/strategist.py`, `tests/unit/config/test_strategist_config.py`

- [ ] Write a failing test asserting `get_strategist_config()` exposes
      `material_trim_fraction == 0.25`, `position_thesis_caps.watch_note_max_chars == 200`
      and `position_thesis_caps.max_rendered_position_events == 3`. Run it, watch it fail.
- [ ] Add the three keys to `config/strategist.json`. `material_trim_fraction` sits at the
      top level beside `slack_percent` (it governs execution bookkeeping, not a prose cap);
      the other two join the existing `position_thesis_caps` block.
- [ ] Extend the Pydantic config model in `src/config/strategist.py` to match. Follow the
      existing pattern in that file — do not add a bare `dict` passthrough.
- [ ] Document all three in `config/README.md` under the `strategist.json` section, stating
      valid ranges: `material_trim_fraction` in (0, 1]; the two caps positive integers.
- [ ] **Delete the orphaned `position_thesis_caps.last_review_note_max_chars`** — all five
      sites: `config/strategist.json:11`, `config/README.md:433`, the field *and its
      docstring paragraph* in `src/config/strategist.py` (`:108` and `:114`), and the value
      in `tests/unit/config/test_strategist_config.py:39`. Verified 2026-08-12: no field on
      `PositionThesis` and no prompt surface consumes it; the config test is its only
      reader. It is a stale artefact of a `last_review_note` field that no longer exists,
      and leaving it beside three genuinely-live new caps invites a future reader to wire
      it up.
- [ ] Run the test, watch it pass. Lint. Commit.

**Requirements**
- The `material_trim_fraction` read site does not exist yet (Task 6) — this task only
  establishes the config surface. Do not add the `# HIGH-VALUE TUNING KNOB:` marker to the
  JSON; it belongs at the Python read site.

---

## Task 4 — Schemas: `Commitment`, `PositionEvent`, `WatchNote`, `PositionThesis`

**Files:** `src/agents/strategist/position_thesis.py`,
`tests/unit/agents/strategist/test_position_thesis.py`,
`tests/fixtures/position_thesis_v1.json`

- [ ] Write failing tests asserting:
      - `Commitment` accepts `{analyst, lean, horizon_days, anchored_at, anchored_tick_id,
        contradicted_since}` and rejects `lean="neutral"` (a commitment is directional by
        construction — a neutral verdict never creates one, so a neutral commitment is
        unrepresentable);
      - `PositionEvent` accepts `{kind="trim", at, tick_id, weight_before, weight_after}`
        and rejects `weight_after >= weight_before` (a trim reduces);
      - `WatchNote` accepts `{ticker, note, last_reviewed_at, last_reviewed_decision}`
        and forbids extras;
      - `PositionThesis` now **requires** `opened_at`, `opened_tick_id`, `opened_price`
        and `weight` — constructing one without them raises `ValidationError`;
      - `PositionThesis` no longer accepts `thesis_last_updated_tick` or
        `thesis_last_updated_at` (rejected by `extra="forbid"`);
      - `commitments` and `position_events` default to `[]`;
      - the frozen V1 fixture still deserialises.
- [ ] Implement the three new models in `position_thesis.py`. All `datetime` fields are UTC
      by convention; the module docstring's "Timestamps" section covers them.
- [ ] Promote `PositionThesis`'s four entry fields to required and delete
      `thesis_last_updated_tick` / `thesis_last_updated_at` outright, along with the
      docstring paragraphs describing them (module docstring "One book of theses" section
      and the class docstring's field-lifecycle bullets). Rewrite the "One book of theses"
      section to describe the two-store split instead — it currently states the opposite
      of what this plan makes true.
- [ ] Add `commitments: list[Commitment] = []` and `position_events: list[PositionEvent] =
      []` with defaults, per the schema-evolution gate in the module docstring.
- [ ] Confirm `tests/fixtures/position_thesis_v1.json` still validates unchanged — it
      already carries all four entry fields and neither staleness field, so it should pass
      untouched. If it does not, STOP and re-read rather than editing the fixture: the
      fixture is the gate, and needing to change it means the schema change is wider than
      the spec authorised.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- Do NOT add `max_length` to `WatchNote.note`. Prose bounds are stated in the prompt only
  (the Vertex pad-toward-cap pathology); `watch_note_max_chars` is a prompt-side cap.
- `model_config = ConfigDict(extra="forbid")` on every new model, matching `PositionThesis`.

---

## Task 5 — `derive_commitments` (pure)

**Files:** `src/agents/strategist/commitments.py` (new),
`tests/unit/agents/strategist/test_commitments.py` (new)

The whole point of T2 in the spec is that this function is the only writer. Keep it pure —
no state mutation, no I/O — mirroring `_verb_dispatch.py`'s design note.

- [ ] Write failing tests, one per row of the spec's write-rules table:
      - directional verdict, no existing commitment → created with the verdict's own
        `horizon_days`, `anchored_at = as_of`, `contradicted_since is None`;
      - directional, same lean, existing commitment → `anchored_at` advances to `as_of`
        and `contradicted_since` is cleared;
      - directional, **opposite** lean → `lean`, `horizon_days` and `anchored_at` are all
        unchanged, and `contradicted_since` is set to `as_of`;
      - opposite lean when `contradicted_since` is already set → **not** overwritten (it
        records when the contradiction began, not when it was last seen);
      - neutral / abstain / `is_no_data` verdict → no create, no contradict, existing
        commitment untouched;
      - expired commitments (`(as_of - anchored_at).days >= horizon_days`) are pruned from
        the returned list;
      - an `as_of` earlier than an `anchored_at` raises `ValueError` rather than yielding a
        negative elapsed count — a commitment with more runway than it was born with is a
        silent-degradation bug of exactly the class this plan exists to stop;
      - the returned list never holds two entries for one analyst, and never more than
        three entries, under any input permutation.
- [ ] Implement
      `derive_commitments(*, prior: list[Commitment], verdicts: dict[str, AnalystVerdict |
      None], as_of: datetime, tick_id: str) -> list[Commitment]`, where `verdicts` is keyed
      by analyst name. Prune expired entries first, then apply the write rules, then return
      sorted by analyst name for stable prompt diffs.
- [ ] Add a `commitment_is_live(c, as_of) -> bool` helper in the same module and use it for
      both pruning here and rendering in Task 8 — one definition of "live", not two.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- Elapsed is **calendar days**: `(as_of - anchored_at).days`. Do not import a market
  calendar; do not count trading sessions. The rest of the system — forward-return
  horizons, the fundamental filing decay, the news carry's `day 12/20` render — is all
  calendar-day, and a second clock behind the same `day N/H` notation is the exact hygiene
  failure this plan is meant to remove.
- The function must not read config. `horizon_days` comes off the verdict, so a later
  config change to a horizon cannot retroactively rewrite a live commitment.
- A verdict whose `horizon_days` is missing or ≤ 0 is a Plan 3c contract violation, not
  something to paper over: raise `ValueError` naming the analyst and ticker.

---

## Task 6 — Verb dispatch across two stores, plus material trims

**Files:** `src/agents/executor/_verb_dispatch.py`,
`tests/unit/agents/executor/test_verb_dispatch.py`

- [ ] Write failing tests for the spec's verb table, twelve cells in total. In particular:
      - `buy` on an existing watch note **promotes**: a `PositionThesis` is returned, the
        buy stance's rationale becomes `rationale`, and the caller is told to drop the
        watch note;
      - `buy` on a live position leaves `opened_at` / `opened_tick_id` / `opened_price`
        frozen while `weight` and `rationale` refresh;
      - `sell` on a watch note returns `HALLUCINATED` (it did not previously — a
        no-position *thesis row* did, and watch notes inherit that);
      - a partial `sell` crossing `material_trim_fraction` appends exactly one
        `PositionEvent`; one below it appends none; both update `weight`;
      - a partial `sell` never touches `opened_at`, `commitments`, or any existing
        `position_events` beyond the append;
      - a full `sell` returns the close sentinel and seeds **no** watch note;
      - `update` on a ticker with no row seeds a `WatchNote`, not a `PositionThesis`.
- [ ] Rewrite `apply_stance_to_thesis` to take `prior_position: PositionThesis | None` and
      `prior_note: WatchNote | None` and return a discriminated result the caller can act
      on for both stores. Keep the `HALLUCINATED` sentinel class and its "log + skip +
      count" policy exactly as-is — that behaviour is unchanged and its
      `"hallucinated_stance"` log key is consumed by the reporting layer's aggregator.
- [ ] Delete `_has_live_position` and every `opened_at is None` discriminator branch. The
      store the row came from is now the discriminator.
- [ ] Delete the `current_tick_index` parameter and its four write sites
      (lines ~175/191/250/262 in the current file), plus the docstring paragraph describing
      it and the `no_action` comment explaining what it deliberately does not reset.
- [ ] Read `material_trim_fraction` from `get_strategist_config()` at the trim site and
      mark it `# HIGH-VALUE TUNING KNOB:` with the trade-off written out — too low and the
      block fills with routine rebalancing noise, too high and a partial exit that changes
      what the position means goes unrecorded.
- [ ] Update the module docstring's verb-vocabulary section to describe the two stores.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- The full-close path must stay compatible with Task 1: the sentinel means "the caller may
  drop this row **if the close executed**". Do not reintroduce unconditional deletion.
- Commitment writing does **not** happen here — this function stays pure and knows nothing
  about verdicts. The executor calls `derive_commitments` and merges the result (Task 7).

---

## Task 7 — Executor wiring

**Files:** `src/agents/executor/agent.py`,
`tests/integration/test_executor_with_fake_broker.py`,
`tests/integration/test_strategist_executor_e2e.py`

- [ ] Write a failing integration test asserting that after a `buy` executes with all three
      analysts directional, the resulting `user:positions` row carries three commitments
      with the right analysts, leans and horizons — and that a second `buy` two days later
      on a same-lean tick advances `anchored_at` without duplicating any entry.
- [ ] Write a failing test asserting `user:watch_notes` is written as its own state key and
      that no row in `user:positions` has a null `opened_at` at any point.
- [ ] In `_assemble_positions` (currently around line 449, writing `user:positions` at
      ~641), read the three canonical `*_verdicts` batches off state, project each to the
      per-ticker `AnalystVerdict`, and call `derive_commitments` for every ticker whose
      stance was a `buy`. Merge the result into the row before it is written.
- [ ] Write `user:watch_notes` alongside `user:positions`. Keep the existing discipline
      documented at `agent.py:328-334`: both stores are written **only** by the
      post-execution callback, never mid-loop, so the strategist's in-tick reads see the
      prior book.
- [ ] Extend Task 1's reconciliation to cover the split: it now checks broker holdings
      against `user:positions` only, and additionally asserts no ticker appears in both
      `user:positions` and `user:watch_notes`. Both are raises.
- [ ] Delete the `current_tick_index` reads at `agent.py:163` and `agent.py:578` and the
      comment at `agent.py:575` describing them.
- [ ] A `buy` that produces zero commitments means every analyst was neutral, abstaining or
      no-data on a ticker the strategist chose to buy. That is possible but notable: log it
      at WARNING with a stable key (`buy_without_commitment`) and the ticker, so the
      validation pass in Task 11 can count it. Do not raise — the strategist is permitted
      to buy on its own read.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- ISO-stringify every datetime before it lands in the state delta. `Commitment` and
  `PositionEvent` go through `model_dump(mode="json")`, not `model_dump()`.

---

## Task 8 — Render: position blocks, commitment line, watch notes

**Files:** `src/agents/strategist/context_shim.py`,
`tests/unit/agents/strategist/test_context_shim.py`

- [ ] Write failing tests asserting the rendered block for a position with three
      commitments contains `Bought on:` with `technical bullish day 41/60`,
      `news bullish day 5/20 (CONTRADICTED since 10-08)` and
      `fundamental bullish day 41/90`; that an expired commitment is absent; that a
      position with a material trim renders a `Trimmed:` line showing at most
      `max_rendered_position_events` entries, most recent first; and that the
      `Thesis updated:` line is **gone**.
- [ ] Write a failing test asserting `render_watch_notes` emits one line per note, sorted
      by ticker, in the form `TICKER — note   (reviewed MM-DD)`, and that the note is
      truncated to `watch_note_max_chars`.
- [ ] Implement both renders. Day counts are calendar days from `anchored_at`; `live` comes
      from `commitment_is_live` — do not re-derive either. The `day N/H` notation
      deliberately matches the existing news-carry render (`day 12/20`), which is the
      format the model has already been reading all run.
- [ ] Delete `_fmt_updated_date`, the `"  Thesis updated: {date}"` line, the `has_position`
      branch, the `[NO POSITION]` tag, and the defensive comment at ~754-757 about
      "watched-only rows whose `[POSITION]` tag predates an executed exit" — that state is
      now unrepresentable, and Task 1 removed the bug that produced it.
- [ ] Hydrate a new `temp:watch_notes_view` in `hydrate` alongside
      `temp:held_positions_view`, reading `state["user:watch_notes"]`.
- [ ] Update the module docstring (lines ~13, ~30, ~45, ~76) — it currently documents the
      single-book model and names `thesis_last_updated_at` explicitly.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- The unrealised-P&L overlay keeps using `live_pos.avg_cost`, not `opened_price` — that is
  existing correct behaviour for added-to positions and is out of scope here.

---

## Task 9 — New prompt content

**Files:** `src/agents/strategist/prompts.py`, `src/contract/strategist_prompt.py`,
`tests/unit/contract/test_strategist_prompt_layout.py`

Task 2 cleaned this surface; this task is the only one permitted to grow it.

- [ ] Write a failing test asserting the assembled prompt contains a `## Watch Notes`
      heading resolving `{temp:watch_notes_view}`, and the accountability paragraph's
      operative sentence.
- [ ] Write a failing test asserting the rendered prompt contains **no** `anchor:` line
      built from `ma200_flip_days` for any input — including one where `ma200_flip_days`
      and `ma200_state` are both present, which is the case the clause was written for.
- [ ] Add the `## Watch Notes` section to the template, immediately after
      `## Thesis Book` / `{temp:held_positions_view}`.
- [ ] Add the accountability paragraph verbatim from the spec's "Prompt instruction"
      section under the Thesis Book heading.
- [ ] Delete the dead anchor clause at `strategist_prompt.py:741-750` and its 724-728
      comment block. Leave the per-verdict `horizon: ~{H}d` render untouched — it works and
      renders every tick. (Task 2 already removed the prompt prose that described this
      clause.)
- [ ] Update the `## Thesis Book` heading text to reflect that it now holds held positions
      only, and the `## Your Job` line "You hold a thesis on every watchlist ticker —
      whether or not you currently own it", which the split makes false.
- [ ] Re-measure the rendered prompt and compare against Task 2's post-sweep number. The
      new content should cost well under the headroom the sweep freed; record both numbers
      in the commit message.
- [ ] Run tests, watch them pass. Lint. Commit.

**Requirements**
- Do not touch `extractors/technical.py`. `ma200_flip_days` / `ma200_state` are correct
  code that will populate once the cache carries 200 bars (spec, Removals §2).

---

## Task 10 — Test-suite migration

**Files:** `tests/` only — never `src/` or `scripts/`.

The following tests reference removed fields and will be red after Tasks 4–9:
`tests/integration/test_multi_tick_backtest_produces_diverse_rationale.py` (asserts on the
`"Thesis updated: YYYY-MM-DD"` render line), `tests/integration/test_executor_with_fake_broker.py`,
`tests/integration/test_thesis_persistence_round_trip.py`,
`tests/integration/test_strategist_executor_e2e.py` (asserts
`thesis_last_updated_tick` advances on `update`).

- [ ] Run the full suite. Record the failure list before changing anything.
- [ ] Migrate each failing test to the new contract. Where a test's *purpose* was to assert
      prose-staleness tracking, replace the assertion with the equivalent commitment
      assertion rather than deleting the test — the intent (the strategist can tell how old
      its view is) survives; only the mechanism changed.
- [ ] `test_strategist_executor_e2e.py`'s scenario docstring names the removed field; update
      it so the file's stated purpose matches what it now checks.
- [ ] Run the full suite, watch it pass. Lint `src/`. Commit.

**Requirements**
- Scope is `tests/` only. If a script under `scripts/` looks dead or broken by this change,
  surface it as a question — do not edit or delete it. `scripts/replay_backtest.py` in
  particular is a manual tool and is not test-driven.

---

## Task 11 — Validation against real run data

**Files:** `docs/Phase14-analyst-refactor/` (a findings note), no `src/` changes expected.

The `ma200_flip_days` lesson is the whole reason this task exists: a correct extractor with
passing unit tests produced the feature in 0 of 600 real rows. The DOV defect is the second
reason: a rejected order silently deleted a thesis row and nothing in the suite noticed.
Fixtures cannot catch either.

- [ ] Re-run the same 30-tick first month as `first-month-6`, so the comparison is
      like-for-like:
      ```
      PYTHONPATH=src .venv/bin/python -m scripts.backtest_run \
        --window long-baseline-2025 --limit 30 --fresh --run-id first-month-7
      ```
      `--fresh` is not optional: without it the run inherits the prior run's thesis book
      from `session.sqlite`, which under the new schema is rows this code cannot validate.
- [ ] Assert against the run's own artefacts, not fixtures:
      - every `[POSITION]` block renders ≥1 commitment across every tick;
      - commitments per position never exceed 3, never exceed 1 per analyst;
      - `CONTRADICTED` appears on roughly **2.1%** of ticker-ticks and mean live
        commitments per ticker-tick lands near the simulated **2.57**. Both simulated
        figures were computed on trading-session ageing; under calendar-day ageing
        commitments expire *sooner* in wall-clock terms, so expect the live count to land
        somewhat **below** 2.57. A large divergence in either direction means the
        derivation is not reading the verdicts the simulation read — investigate before
        reading any P&L number;
      - `user:positions` holds zero rows with a null `opened_at` at every tick, and no
        ticker appears in both stores;
      - **the Task 1 reconciliation never raised**, and every broker holding has a thesis
        row at every tick. Confirm DOV specifically;
      - the `buy_without_commitment` WARNING count from Task 7.
- [ ] Record the headline before/after: median holding period (baseline 6 sessions),
      realised P&L across closed round-trips (baseline −$1,196 on 9 closes), win rate
      (baseline 22.2%), round-trip count. Note that Task 1 alone changes the round-trip
      count — DOV's 09-22 close will now execute — so attribute carefully rather than
      crediting the whole delta to commitments.
- [ ] Count `sell`-stance rationales that name a live commitment. Baseline is 0/190
      rationales with any temporal content. **Near-zero after this change means the
      accountability clause is not landing and the lever has failed — report that
      explicitly, independent of whether P&L improved.** A P&L improvement with zero
      temporal rationales is a coincidence, not a result.
- [ ] Record the prompt size trajectory: `first-month-6` baseline 1,581 lines / 115KB,
      post-Task-2, and final. Growing back past the baseline means the sweep was spent
      rather than banked.
- [ ] Write the findings to `docs/Phase14-analyst-refactor/plan3d-validation.md`. Commit.

**Requirements**
- Thesis-prose churn (baseline 9.5 revisions/ticker) is **not** a target and is expected to
  stay roughly flat. Do not tune against it.
- The snapshotter return columns are structurally 0.0 in 30/30 rows
  (`snapshot/agent.py:136-148`, out of scope per the spec). Do not read the validation
  metrics off `portfolio_snapshots.bot_return_pct` — take them from the trade log and
  `report/metrics.md`.
