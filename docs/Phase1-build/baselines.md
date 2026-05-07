# Baselines

Every StockBot evaluation must report performance **side-by-side with two baselines** over the identical universe and time window. If the bot doesn't clearly beat both, the strategy isn't ready.

## Baseline 1 — SPY Buy & Hold

The dumbest possible benchmark, and the one most retail strategies fail to beat.

- **Universe:** SPDR S&P 500 ETF (`SPY`).
- **Strategy:** Buy at t₀ with the same starting capital as the bot. Hold to t_n. Reinvest dividends.
- **Reported metrics:** cumulative return, annualised return, Sharpe ratio, max drawdown, Calmar ratio.
- **Implementation:** `baselines/spy.py` — pulls SPY OHLCV via `yfinance`, computes the metrics with `pandas`.

## Baseline 2 — PyTorch MLP

A minimal supervised-learning baseline. Establishes whether the multi-agent system is doing anything more useful than a single small neural net trained on price features.

- **Architecture:**
  - Input: lagged returns (1d / 5d / 20d), RSI, MACD signal, volume z-score, sector-ETF return.
  - Hidden: 2 fully-connected layers, 64 units each, ReLU, dropout 0.2.
  - Output: scalar — predicted next-day directional probability (sigmoid).
- **Training:** rolling-window walk-forward, retrained monthly. Loss = BCE. Optimiser = Adam, lr 1e-3.
- **Trade rule:** go long when `p > 0.55`, flat when `0.45 ≤ p ≤ 0.55`, go short when `p < 0.45` (subject to broker constraints — short may be disabled for Trading 212 practice).
- **Implementation:** `baselines/mlp.py` — uses `torch`, features built with `ta`, scaling with `scikit-learn`.
- **Reported metrics:** identical to Baseline 1 plus directional accuracy and turnover.

## Evaluation Harness

`baselines/evaluate.py` runs the StockBot, the SPY baseline, and the MLP baseline through the same backtester, then writes a single comparison report into `docs/performance/<date>.md` with:

1. Summary metric table (3 rows × N metrics).
2. Equity-curve plot for all three.
3. Drawdown plot for all three.
4. A pass/fail line: "Bot beats both baselines on Sharpe and on cumulative return — yes / no."

## Ground Rules

- **Same starting capital, same fees, same slippage model** for all three. Anything else is a rigged comparison.
- **Same universe.** If the bot trades only US large-caps, the MLP baseline trades the same set; SPY remains SPY because that's the market proxy.
- **Same time window.** No cherry-picking favourable years for one and not the others.
- **Re-run baselines whenever the universe or the evaluation window changes.**
