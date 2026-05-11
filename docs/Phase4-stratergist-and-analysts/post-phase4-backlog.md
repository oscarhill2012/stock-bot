# Post-Phase-4 Backlog

Polish-grade follow-ups consolidated from the Phase 4 chunk audits. None of these block any future plan — pick them off opportunistically.

**Routing summary (where each Phase-4 follow-up went):**

- **Plan D, Task D9** absorbs `FU-06`, `FU-08`, `FU-09`, `FU-15`, `FU-16`, `FU-17`, plus the `db→session` test-fixture rename (`FU-20`) opportunistically.
- **Plan E** (strategist hardening, separate file) absorbs `FU-01` through `FU-05`.
- **This file** tracks the rest — items that don't belong inside a plan because they're isolated, cross-cutting, or tooling-shaped.

**Already resolved on main** (excluded from the table — listed here only so future readers don't reopen them):

| Commit | Item |
|---|---|
| `04e2575` | Ruff `--fix` on the four Plan-C unit test files (I001, F401, UP017) |
| `3aba297` | Untrack `data/stockbot.db` + add `.gitignore` entries for its sidecars |
| `80b4cb1` | C15 `@pytest.mark.integration` decorator on the v2 smoke |
| `b79f223` | C16 `__import__("datetime").timezone.utc` polish in `save_portfolio_snapshot` |
| `5bd6567` | C14 pre-existing I001 fix on `tests/integration/test_pipeline_composition.py` |

---

## Open items

| ID | Sev | Area | Item | Source |
|---|---|---|---|---|
| FU-07 | Important | schema | Introduce a shared `Ticker` type alias (`Annotated[str, Field(min_length=1, pattern=r"^[A-Z]+$")]`) and apply it across `contract/` and `strategist/` schema fields in a single pass. Bare `ticker: str` currently accepts empty strings. Best landed as one focused commit *before* Plan D starts (so Plan D's new evidence rows pick up the type) — or *after* Plan D with a wider sweep. | Chunk 1 C1 quality review |
| FU-10 | Nice | held_view | Add a `logging.warning(...)` call in `src/agents/strategist/held_view.py` when a corrupt thesis dict is silently skipped (currently swallowed by `except Exception  # noqa: BLE001`). Deferred pending a central logging pass. | Chunk 1 audit follow-up #2 |
| FU-11 | Nice | derivation | Promote the hard-coded `"swing"` horizon default at `src/agents/strategist/derivation.py:136` to a `DEFAULT_HORIZON: Final[str]` constant (or move to a shared constants module). | Chunk 1 audit follow-up #3 |
| FU-12 | Nice | schema | Clarify the `PositionThesis.preferred_weight` / `stance_schema.py` docstring wording around the `[0.0, 1.0]` Pydantic bound vs. the "risk-gate clamp" — current phrasing implies two separate clamps exist. | Chunk 1 audit follow-up #4 |
| FU-13 | Nice | evidence_view | Replace the hard-coded four-analyst tuple at `src/agents/strategist/evidence_view.py:57` with `typing.get_args(AnalystName)` so the view automatically picks up any future fifth analyst without a code change. | Chunk 1 audit follow-up #5 |
| FU-14 | Nice | style | Normalise docstring style across `src/agents/strategist/`: C1 and C5 use Google `Args:`/`Returns:` format; C2, C4, and C6 use NumPy `Parameters\n----------` format. Pick one convention and apply in a single sweep. | Chunk 1 Opus audit follow-up #1 |
| FU-18 | Nice | tests | Simplify the vacuous `except (AttributeError, BaseException)` tuple at `tests/integration/test_strategist_v2_smoke.py:186` — `BaseException` already subsumes `AttributeError`. Tighten to `except BaseException:`. | Chunk 4 C15 quality advisory |
| FU-19 | Nice | CI / tooling | Extend the ruff gate (or add a pre-commit hook) to cover `tests/` as well as `src/`. The Chunk-1–3 test-file ruff debt that landed in `04e2575` accumulated silently because CI doesn't lint test files today. | Chunk 4 Opus audit root-cause finding |

---

## Notes on item selection

- **FU-20 (test fixture rename `db` → `session`)** is folded into Plan D Task D9 Step 2 because the file-set overlaps with the `sessionmaker → Session(bind=)` sweep. If Plan D ships without absorbing it, lift the rename here as its own row.
- **Plan E's items (FU-01 to FU-05)** are not duplicated here — they live entirely in `plan-E-strategist-hardening.md`. If Plan E is ever cancelled, copy them back into this table.
- **FU-19 (CI ruff gate for `tests/`)** is the only tooling-shaped item left. Worth doing soon to prevent a recurrence of the Chunk-3 test debt situation; not blocked on Plan D or Plan E.
- **Severity legend:** *Important* = quietly produces wrong results or hides misconfiguration; *Nice* = legibility, future-proofing, or cosmetic only.
