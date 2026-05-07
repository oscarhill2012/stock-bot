# 2026-05-06 — Initial Setup

## What landed today

- **Project scaffolding:** `CLAUDE.md` documenting architecture + workflow.
- **Docs folder bootstrapped** with `README.md`, `data-sources.md`, `baselines.md`.
- **Six canonical data functions** mapped to libraries (`finnhub-python`, `yfinance`, `sec-api`, `requests`-against-Quiver). See `data-sources.md`.
- **Two baselines locked in:** SPY buy & hold, PyTorch MLP. See `baselines.md`.
- **`requirements.txt`** with data, agent, baseline, and tooling dependencies.
- **`.env`** template with placeholders for every API key the data layer + broker will need.

## Open questions for next session

- Which Finnhub plan are we on? Free tier blocks social sentiment on some plans — needs verification before we wire `get_social_sentiment`.
- Trading 212 API access has to be requested manually — start the application now so it isn't the long-pole later.
- Decide whether Phase 1 universe is "SPY constituents" or a smaller hand-picked watchlist (the latter keeps Finnhub free-tier within budget).

## Next steps

1. Create the directory skeleton (`data/`, `agents/`, `broker/`, `orchestrator/`, `baselines/`, `tests/`).
2. Write the Pydantic models in `data/models/` so every provider has a target shape.
3. Implement `get_stock_stats` first — no API key needed, fastest validation that the wiring works end-to-end.
4. Stand up the SPY baseline so we have a number to beat from day one.
