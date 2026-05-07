# PyTorch MLP Model — Feature Specification

Replaces the sketch in `baselines.md` with a concrete 11-feature MLP backed entirely by `yfinance` OHLCV data. No external data provider required.

---

## Features

All features are computed from daily OHLCV bars pulled via `yfinance`. Column names use the snake_case convention throughout the codebase.

| # | Feature | Formula |
|---|---------|---------|
| 1 | `daily_return_1d` | `(close_t / close_{t-1}) - 1` |
| 2 | `daily_return_3d` | `(close_t / close_{t-3}) - 1` |
| 3 | `daily_return_5d` | `(close_t / close_{t-5}) - 1` |
| 4 | `daily_return_10d` | `(close_t / close_{t-10}) - 1` |
| 5 | `volume_change_1d` | `(volume_t / volume_{t-1}) - 1` |
| 6 | `volume_change_5d` | `(volume_t / volume_{t-5}) - 1` |
| 7 | `volatility_5d` | Rolling 5-day std of `daily_return_1d` |
| 8 | `volatility_20d` | Rolling 20-day std of `daily_return_1d` |
| 9 | `moving_average_gap_5d` | `(close_t / SMA_5) - 1` |
| 10 | `moving_average_gap_20d` | `(close_t / SMA_20) - 1` |
| 11 | `rsi_14` | 14-period RSI (Wilder smoothing), scaled to [0, 1] by dividing by 100 |

Feature vector length: **11**.

All features are normalised using a rolling `StandardScaler` fitted on the training window only — no look-ahead.

---

## Architecture

```
Input  (11)  →  Linear(11 → 64)  →  ReLU  →  Dropout(0.2)
             →  Linear(64 → 32)  →  ReLU  →  Dropout(0.2)
             →  Linear(32 → 1)   →  Sigmoid
```

- Output is a scalar probability: `p ∈ (0, 1)` — the predicted probability that next-day close is higher than today's close.
- Loss: Binary Cross-Entropy (`BCELoss`).
- Optimiser: Adam, lr = 1e-3, weight decay = 1e-4.
- Epochs per training window: 50 (early stopping if val loss doesn't improve for 10 epochs).

---

## Label

```
y_t = 1  if  close_{t+1} > close_t  else  0
```

The model is trained to predict tomorrow's direction, not magnitude.

---

## Training Procedure

Walk-forward cross-validation to avoid look-ahead:

1. Use a 2-year rolling window of daily bars as the training set.
2. Validate on the following 3-month window.
3. Retrain monthly — slide the window forward by 21 trading days.
4. Fit a fresh `StandardScaler` on each training fold before scaling both train and validation splits.

Minimum history required before first prediction: **252 trading days** (~1 year).

---

## Decision Rule

| Condition | Action |
|-----------|--------|
| `p > 0.55` | Go long |
| `0.45 ≤ p ≤ 0.55` | Hold / flat (no trade) |
| `p < 0.45` | Short (or flat if shorting disabled on broker) |

The dead band (`0.45–0.55`) reduces turnover on low-confidence predictions.

---

## Data Pipeline

```python
import yfinance as yf

df = yf.download(ticker, period="5y", interval="1d", auto_adjust=True)
# columns used: Close, Volume
```

Feature computation order matters — compute returns first, then volume changes, then rolling stats, then RSI. Drop the first 20 rows (NaN warm-up from the longest rolling window).

---

## Implementation Target

- **File:** `baselines/mlp.py`
- **Class:** `MLPBaseline` — exposes `.fit(df)`, `.predict(df)` and `.backtest(df)` methods.
- **Dependencies:** `torch`, `yfinance`, `pandas`, `scikit-learn`, `ta` (for RSI).

---

## Evaluation

Run through the shared harness in `baselines/evaluate.py`. Metrics reported:

- Directional accuracy (%)
- Cumulative return
- Annualised return
- Sharpe ratio
- Max drawdown
- Calmar ratio
- Turnover (trades per month)

Side-by-side comparison with SPY buy-and-hold as documented in `baselines.md`.
