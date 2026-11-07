# reversal_trail target study, 2026-11-07: nothing passes, and the 50% target stays

- **Script:** `research/reversal_target_study.py`, pre-registered before running.
- **Exit logic:** `atlas_trader/reversal_exits.py`, tested in `tests/test_reversal_exits.py`. The tests cover no look-ahead in the targets and the trail switch, and the half-close lot sizing.
- **Run:** in `/opt/atlas-research` only. Nothing in `/opt/atlas-trader`, `config/default.yaml` or `state/` was touched, and nothing was deployed.

**Setup:**

- **Entries:** the current reversal_trail entries (research copy: 1m, move 5 ATR, RSI 25/75, entries 08:00–11:00 NY).
  - 59 trades in half A, 64 in half B.
  - Every variant uses exactly these trades.
- **Stop rules:** unchanged (initial stop beyond the move's extreme, candle trail from +1R).
- **Data:** GatesFX XAUUSD.R 1m, 2025-10-01 to 2026-11-05.
  - Half A: up to 2026-04-10.
  - Half B: from 2026-04-12.
- **Costs:** spread 0.14 + slippage 0.10, $3.50 per lot per side; R is net of commission.
- **Built-in checks, both passed:**
  - The simulator reproduced the backtester's R on every baseline trade.
  - The recorded move reproduced every live 50% target exactly.

**Notes on the variants:**

- **38.2%:** on 22 of 123 trades the level was already behind the entry fill, so those trades took the target at once and lost about the costs.
- **Split:** every trade was big enough to split into two halves.

## Results

Half-B columns are on the untouched half; max drawdown is in R with trades in entry order; t is the paired per-trade difference against the baseline.

| Variant | Half A R | Half B R | B win % | B exp R | B max DD R | B paired ΔR/trade | B paired t |
|---|---|---|---|---|---|---|---|
| **1. baseline: 50% of the move** | **+0.0** | +9.5 | 67.2 | +0.149 | 3.7 | — | — |
| 2. 38.2% | −8.6 | +1.6 | 64.1 | +0.026 | 4.0 | −0.123 | −1.94 |
| **3. 61.8% (picked on A)** | −0.3 | **+13.0** | 62.5 | +0.203 | **6.0** | +0.054 | 0.78 |
| 4. 78.6% | −4.8 | +16.8 | 60.9 | +0.263 | 4.1 | +0.114 | 1.30 |
| 5. 100% (back to the move's start) | −3.3 | +13.8 | 56.3 | +0.216 | 5.7 | +0.067 | 0.63 |
| 6. split: half at 50%, rest trails from 1R | −1.7 | +11.4 | 56.3 | +0.178 | 4.7 | +0.029 | 0.54 |
| 7. switch: no target, trail from 0R once 50% is reached | −2.7 | +4.3 | 54.7 | +0.067 | 5.8 | −0.082 | −0.99 |

**Over both halves (all 123 trades):**

| Variant | Total R | Paired ΔR/trade | t |
|---|---|---|---|
| baseline 50% | +9.6 | — | — |
| 38.2% | −7.0 | −0.135 | −3.36 |
| 61.8% | +12.7 | +0.025 | 0.49 |
| 78.6% | +12.1 | +0.020 | 0.29 |
| 100% | +10.6 | +0.008 | 0.10 |
| split | +9.7 | +0.001 | 0.03 |
| switch | +1.6 | −0.065 | −1.15 |

**Pass bar:** the variant picked on half A was the 61.8% target. On half B:

- **Total R:** +13.0R against the baseline's +9.5R (ok).
- **Expectancy:** +0.203R (ok).
- **Max drawdown:** 6.0R against a limit of 4.5R, which is 1.2 × the baseline's 3.7R (fail).
- **Paired difference:** +0.054R per trade, t 0.78, n 64.

**Result: NOT PASSED.** The holdout step was not reached, so the untouched 2023–25 data is still clean. Running it would also have needed 1m data for 2023–25, and the 1m history on disk starts 2025-10-01.

## Verdict

**Nothing passes, so the 50% target stays.**

- **Deeper targets flip between halves.** 61.8%, 78.6%, 100% and the split all lost to the baseline on half A and won on half B. Over all 123 trades they come out within +0.03R per trade of the 50% target (t ≤ 0.5). That is the same pattern seen in the wick-trail studies, and it is noise, not an edge.
- **The picked 61.8% target failed the risk check.** Its half-B drawdown was 6.0R against the baseline's 3.7R.
- **Two variants are clearly worse and can be ruled out:**
  - The 38.2% target lost in both halves (−7.0R over 13 months against +9.6R, t −3.36).
  - "No target, trail after 50%" gave back most of the gains (+1.6R in total).
- **The model's edge itself is thin.** The 50% baseline makes +0.078R per trade over 123 trades, and no target choice changes that meaningfully.

**Reproduce:**

```
PYTHONPATH=. python research/reversal_target_study.py data/raw/XAUUSD.R_1m.csv
```

This writes `research/reversal_target_results.csv` and `research/reversal_target_trades.csv`.
