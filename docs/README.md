# StockBot Docs

Living record of the project: what we're building, why we're building it that way, and how it's performing.

## Layout

| Folder / file | Purpose |
|---|---|
| `data-sources.md` | The six canonical data functions and which library backs each one. |
| `baselines.md` | The two reference benchmarks (SPY buy-and-hold + PyTorch MLP). |
| `decisions/` | One file per architectural decision (ADR-style). |
| `performance/` | Time-stamped backtest + paper-trading reports. |
| `YYYY-MM-DD-*.md` | Dated milestone / progress logs at the root. |

## Conventions

- Dates use ISO format (`YYYY-MM-DD`).
- Every experiment writes a performance report into `performance/` with the same timestamp prefix as its log entry — they are findable as a pair.
- Decisions get an ADR file the moment we lock them in; if we revise later, we **append** a "Superseded by …" note rather than overwriting.

## Current Phase

**Phase 1 — Practice / Training.** All trades route through the Trading 212 demo account. We compare every strategy against the two baselines in `baselines.md` before considering Phase 2 (live capital).
