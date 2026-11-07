# P1/P2 wick-trail study, 2026-11-07: nothing passes, the baseline trail stays

- **Script:** `research/wick_trail_study.py`, pre-registered before running.
- **Exit logic:** `atlas_trader/wick_trail.py`, tested in `tests/test_wick_trail.py`. The tests cover no look-ahead, a forward-only stop, and that confirmation needs a closed strong candle.
- **Run:** in `/opt/atlas-research` only, at low priority and one job at a time. Nothing in `/opt/atlas-trader`, `config/default.yaml` or `state/` was touched.
- **Data:** GatesFX XAUUSD.R 1m, 2025-10-01 to 2026-11-05 (388,922 bars).
  - Half A: 2025-10-01 to 2026-04-10.
  - Half B: 2026-04-12 to 2026-11-05.
- **Costs:**
  - Spread 0.14, slippage 0.10, $3.50 per lot per side.
  - A stop and target inside the same bar count as a stop.
  - Gaps fill at the open.
  - R is net of commission. Earlier docs left commission out of R, so these numbers run a little lower.
- **Same entries:** entries come from the normal backtester and are identical for every exit variant, so trades are compared in pairs.
- **Simulator check:** before any variant ran, the exit simulator replayed the baseline and matched the backtester on every trade (max |ΔR| = 0.0).

**What changed:** only the "trail from 1R" candle trail was replaced. Everything else stayed the same:

- the initial stop
- reversal_trail's 50%-of-move target
- momentum_rsi's 2-ATR big-candle lock (still judged on 5m candles when the trail runs on 1m)
- the 16:50 NY / end-of-day flat

**How the two models were set up:**

- **momentum_rsi:** the shoot-out #1 settings, entries 08:00–11:00 NY.
- **reversal_trail:** the shoot-out #4 settings, entries 08:00–11:00 NY. Its strategy file was deleted from the bot on 2026-11-06, so the study uses an unchanged research copy: `research/reversal_trail_model.py`.
- **Trade counts differ from the shoot-out.** Entries were regenerated with today's code and config, which flattens at 16:50 NY instead of the shoot-out's 15:50. That gives 271 momentum_rsi trades (shoot-out: 241) and 123 reversal_trail trades (shoot-out: 121).
- **Neither model is in the live config now.** The live models are smc_ob and st_confluence.

**Pass bar (fixed before running):**

1. Pick the variant with the best total R on half A.
2. On half B it must beat the baseline's total R, have expectancy > 0, and have a max drawdown no more than 20% worse than the baseline's.
3. STRONG means it passes, and on half B also has n ≥ 100 and a paired t ≥ 2.5.

Max drawdown is peak-to-trough cumulative R, with trades in entry order. The paired t is the per-trade R difference against the baseline on half B.

## momentum_rsi (5m entries): n = 138 in A, 133 in B

| Variant | Half A R | Half B R | B exp R | B win % | B max DD R | B avg R per winner | B paired ΔR/trade | B paired t |
|---|---|---|---|---|---|---|---|---|
| **baseline: candle trail from 1R** | +17.9 | **+24.2** | +0.182 | 38.3 | 13.5 | 2.11 | — | — |
| wick 5m, buffer 0.10 | +24.7 | +18.4 | +0.139 | 36.1 | 16.1 | 2.14 | −0.043 | −1.21 |
| wick 5m, buffer 0.10 + BE | +17.9 | −7.0 | −0.053 | 24.1 | 20.8 | 2.01 | −0.235 | −1.92 |
| **wick 5m, buffer 0.25 (picked on A)** | **+27.3** | +18.5 | +0.139 | 36.1 | 17.0 | 2.15 | −0.043 | −1.26 |
| wick 5m, buffer 0.25 + BE | +21.2 | −6.2 | −0.047 | 24.1 | 19.9 | 2.03 | −0.229 | −1.88 |
| wick 5m, buffer 0.50 | +25.0 | +19.8 | +0.149 | 36.1 | 17.5 | 2.18 | −0.033 | −0.72 |
| wick 5m, buffer 0.50 + BE | +19.3 | −6.9 | −0.052 | 24.1 | 20.3 | 2.01 | −0.233 | −1.92 |
| wick 1m, buffer 0.10 | +11.6 | +15.6 | +0.118 | 37.6 | 9.4 | 1.92 | −0.064 | −0.73 |
| wick 1m, buffer 0.10 + BE | +13.7 | +4.4 | +0.033 | 33.1 | 17.0 | 1.67 | −0.149 | −1.09 |
| wick 1m, buffer 0.25 | +10.6 | +15.6 | +0.117 | 37.6 | 9.8 | 1.93 | −0.065 | −0.74 |
| wick 1m, buffer 0.25 + BE | +11.9 | +4.7 | +0.035 | 33.1 | 15.4 | 1.68 | −0.147 | −1.08 |
| wick 1m, buffer 0.50 | +9.2 | +14.8 | +0.112 | 38.3 | 10.7 | 1.88 | −0.070 | −0.80 |
| wick 1m, buffer 0.50 + BE | +10.9 | +3.2 | +0.024 | 33.1 | 15.0 | 1.65 | −0.158 | −1.16 |

The variant picked on half A was wick 5m with a 0.25 buffer. It failed on half B:

- **Total R:** +18.5R against the baseline's +24.2R (fail).
- **Expectancy:** +0.139R (ok).
- **Max drawdown:** 17.0R against a limit of 16.2R, which is 1.2 × the baseline's 13.5R (fail).
- **Paired difference:** −0.043R per trade, t −1.26, n 133.

**Result: NOT PASSED.**

## reversal_trail (1m entries): n = 59 in A, 64 in B

| Variant | Half A R | Half B R | B exp R | B win % | B max DD R | B avg R per winner | B paired ΔR/trade | B paired t |
|---|---|---|---|---|---|---|---|---|
| **baseline: candle trail from 1R + 50% target** | +0.0 | **+9.5** | +0.149 | 67.2 | 3.7 | 0.68 | — | — |
| all 6 wick variants (1m; buffer 0.10 / 0.25 / 0.50; with and without BE) | +0.0 | +9.5 | +0.149 | 67.2 | 3.7 | 0.68 | 0.000 | 0.00 |

Every variant gave exactly the same result as the baseline on all 123 trades. On reversal_trail the stop never decides the exit:

- The 50%-of-move target sits below +1R on most trades. Only 12 of 123 trades got past +0.9R.
- All 12 of those hit the target before any stop move could matter.

The variant picked on half A (a tie, so the first one listed) did not beat the baseline on half B (+9.5R = +9.5R). **Result: NOT PASSED.**

## Verdict

**Nothing passes. The current "trail from 1R" candle trail stays for both models, and nothing gets deployed.**

- **momentum_rsi, wick trail on 5m without break-even:** it made more R than the baseline on half A (+24.7 to +27.3R against +17.9R) but less on half B (+18.4 to +19.8R against +24.2R). The picked variant's half-B drawdown was also worse than the 20% limit allows. So the half-A edge was noise.
- **momentum_rsi, wick trail on 1m:** the shallowest drawdown on half B (9.4 to 10.7R against 13.5R), but less total R in both halves.
- **Break-even step (P2):** it hurt in every 5m case, and in half B on 1m (on 1m it helped slightly in half A). It cut the win rate from about 36% to 24–33%, because +1R winners were stopped out flat before the move continued.
  - Against the same wick trail without break-even, over 13 months it cost 31–32R on 5m and 9–10R on 1m.
  - Against the baseline, over both halves, the break-even variants lost 0.09 to 0.12R per trade (paired t −1.16 to −1.64).
- **reversal_trail:** the stop never decides the exit, because the target is hit first. P1 and P2 change nothing for this model.

None of the paired differences comes close to significance (best half-B t = −0.72). The results do not show that the wick trail is better or worse than the current trail. They do show it isn't an upgrade worth taking live.

**Reproduce:**

```
PYTHONPATH=. python research/wick_trail_study.py data/raw/XAUUSD.R_1m.csv
```

This writes `research/wick_trail_results.csv` and `research/wick_trail_trades.csv` (per-trade R for every variant). The copies from this run are in `/opt/atlas-research/research/`.
