# P1/P2 wick trail on the live models, 2026-11-07: both "pass" on paper, but there is no real edge over the current trail

- **Script:** `research/wick_trail_live_study.py`, pre-registered before running.
- **Same as the first study:** exit rules, grid, costs (R net of commission) and pass bar as `docs/RESULTS_wick_trail.md`.
- **Entries:** the live settings, entries 08:00–11:00 NY, generated with the archetypes-study settings.
  - The baselines reproduce that study's trade counts: smc_ob 270 trades, st_confluence 113.
- **Simulator check:** the exit simulator replayed every baseline trade exactly (max |ΔR| = 0.0). That includes st_confluence's SuperTrend-flip exit, which is unchanged in every variant.
- **What changed:** only the "trail from 1R" was swapped out.
- **Run:** in `/opt/atlas-research` only.

**Primary run (decides):**

- XAUUSD.R 5m, 2023-03-02 to 2026-11-05, split at the date midpoint as in the archetypes study.
  - Half A: up to 2025-01-02.
  - Half B: from 2025-01-02.
- Trail timeframes: smc_ob 5m; st_confluence 15m and 5m. There is no 1m data for these years.

**Secondary run (information only):** the 13 months of 1m data, adding the 1m trail. It sits entirely inside half B of the primary run, so it is not an independent check.

## smc_ob, primary run: n = 127 in A, 143 in B

| Variant | Half A R | Half B R | B exp R | B win % | B max DD R | B avg R per winner | B paired ΔR/trade | B paired t |
|---|---|---|---|---|---|---|---|---|
| **baseline: candle trail from 1R** | **+4.9** | +46.4 | +0.324 | 40.6 | 22.9 | 2.29 | — | — |
| wick 5m, buffer 0.10 | −1.1 | +48.3 | +0.338 | 39.2 | 24.7 | 2.48 | +0.014 | 0.20 |
| **wick 5m, buffer 0.10 + BE (picked on A)** | −0.9 | **+55.8** | +0.390 | 29.4 | 17.3 | 2.88 | +0.066 | 0.85 |
| wick 5m, buffer 0.25 | −2.7 | +47.4 | +0.332 | 39.2 | 25.0 | 2.47 | +0.007 | 0.11 |
| wick 5m, buffer 0.25 + BE | −2.1 | +52.9 | +0.370 | 29.4 | 17.5 | 2.81 | +0.046 | 0.59 |
| wick 5m, buffer 0.50 | −5.0 | +44.5 | +0.311 | 38.5 | 25.5 | 2.46 | −0.013 | −0.19 |
| wick 5m, buffer 0.50 + BE | −3.8 | +48.7 | +0.341 | 29.4 | 17.9 | 2.71 | +0.016 | 0.21 |

**Pass bar: PASS, not STRONG.**

- **Half B:** +55.8R against +46.4R (ok).
- **Expectancy:** +0.390R (ok).
- **Max drawdown:** 17.3R against a limit of 27.5R (ok).
- **Paired difference:** +0.066R per trade, t 0.85, n 143.

**But:**

- Every variant lost to the baseline on half A. The picked one made −0.9R there against the baseline's +4.9R.
- Over all 270 trades it beats the baseline by only +0.014R per trade (t 0.22).
- Its half-A max drawdown is 19.0R against the baseline's 18.3R, so the lower drawdown shows up only in half B.

**Secondary run (13 months of 1m), same variant:**

- **Results:** +22.3R / −1.3R (A / B) against the baseline's +21.1R / −5.9R. Paired t 1.05.
- **Overlap:** this period lies inside primary half B, so it is the same evidence again, not a new test.
- **1m trail (no BE):** best half B (+4.1 to +7.6R against −5.9R) but much worse half A (+5.2 to +9.9R against +21.1R). That's the same flip-flop again.

## st_confluence, primary run: n = 57 in A, 56 in B

| Variant | Half A R | Half B R | B exp R | B win % | B max DD R | B avg R per winner | B paired ΔR/trade | B paired t |
|---|---|---|---|---|---|---|---|---|
| **baseline: candle trail from 1R** | **+3.3** | +1.9 | +0.034 | 37.5 | 10.4 | 1.33 | — | — |
| wick 15m, buffer 0.10 | +2.9 | +2.7 | +0.049 | 37.5 | 10.1 | 1.37 | +0.015 | 0.77 |
| wick 15m, buffer 0.10 + BE | +2.2 | +3.2 | +0.058 | 32.1 | 10.9 | 1.49 | +0.024 | 0.59 |
| wick 15m, buffer 0.25 | +2.9 | +2.6 | +0.046 | 37.5 | 10.1 | 1.36 | +0.013 | 0.62 |
| wick 15m, buffer 0.25 + BE | +2.2 | +3.1 | +0.055 | 32.1 | 10.9 | 1.48 | +0.021 | 0.52 |
| wick 15m, buffer 0.50 | +2.9 | +2.5 | +0.044 | 37.5 | 10.1 | 1.36 | +0.010 | 0.49 |
| wick 15m, buffer 0.50 + BE | +2.2 | +3.0 | +0.053 | 32.1 | 10.9 | 1.48 | +0.019 | 0.47 |
| wick 5m, buffer 0.10 | +0.2 | +2.7 | +0.048 | 37.5 | 10.1 | 1.37 | +0.014 | 0.69 |
| wick 5m, buffer 0.10 + BE | +1.8 | +3.3 | +0.059 | 33.9 | 10.7 | 1.41 | +0.025 | 0.61 |
| wick 5m, buffer 0.25 | +2.6 | +2.5 | +0.044 | 37.5 | 10.2 | 1.36 | +0.011 | 0.52 |
| **wick 5m, buffer 0.25 + BE (picked on A)** | +3.1 | **+3.1** | +0.055 | 33.9 | 10.8 | 1.41 | +0.022 | 0.53 |
| wick 5m, buffer 0.50 | +2.1 | +2.2 | +0.039 | 37.5 | 10.3 | 1.34 | +0.005 | 0.25 |
| wick 5m, buffer 0.50 + BE | +2.7 | +2.8 | +0.050 | 33.9 | 10.9 | 1.39 | +0.016 | 0.40 |

**Pass bar: PASS, not STRONG.**

- **Half B:** +3.1R against +1.9R (ok).
- **Expectancy:** +0.055R (ok).
- **Max drawdown:** 10.8R against a limit of 12.4R (ok).
- **Paired difference:** +0.022R per trade, t 0.53, n 56. That's well short of 100 trades.

**But:**

- Every variant again lost to the baseline on half A. The picked one made +3.1R there against +3.3R.
- Over all 113 trades the difference is +0.009R per trade (t 0.34). That's about +1R in 3.7 years.
- **Secondary run:** only 29 trades. Every variant was within ±0.5R of the baseline on half B and +0.5 to +2.8R better on half A. The 5m trail wasn't in that run.

## Verdict

**Both models technically pass the pre-registered bar. Neither is STRONG, and the data shows no real edge over the current trail.**

- **Why the bar was met:** it picks the best variant on half A, even one that lost to the baseline there, and then asks only that it win on half B. Here every variant lost on half A and won on half B.
- **Over both halves:** the gains are +0.014R per trade (smc_ob) and +0.009R per trade (st_confluence), with t-stats of 0.2–0.3. That is noise.
- **Break-even step:** on smc_ob it lowered half-B drawdown (17.3R against 22.9R) at the cost of a 29% win rate against 41%. In half A, though, the drawdown was the same or slightly worse.
- **Recommendation:** do not switch the live trail on this evidence. Switching would change how the bot behaves without any proven gain. If Ricardo wants it anyway, it should go in as a demo forward test with the existing cut rule, not as an upgrade. **Nothing has been deployed.**

**Reproduce:**

```
PYTHONPATH=. python research/wick_trail_live_study.py data/raw/XAUUSD.R_5m.csv data/raw/XAUUSD.R_1m.csv
```

The output CSVs are in `/opt/atlas-research/research/wick_trail_live_*`.
