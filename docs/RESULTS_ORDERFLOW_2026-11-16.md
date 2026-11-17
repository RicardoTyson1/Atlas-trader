# Order-flow gate study: results (2026-11-16)

Pre-registration: docs/PREREG_ORDERFLOW_2026-11-16.md. Script: research/orderflow_gate_study.py.
Run in /opt/atlas-research on the server, 2026-11-16 19:55-20:04 NY (nice 19; atlas@default untouched).
Data: GatesFX XAUUSD.R 5m 2023-03-02 -> 2026-11-05, split 2025-01 (halves A/B), real spread, slippage, $7/lot.
GC volume: Databento GC 5m (2022-01 onward slice of data/raw/GC_5m.csv); 2.3% (A) / 4.3% (B) of bars had no GC feature (they fail every gate).
Not financial advice. Research only: nothing in config/default.yaml changed.

## Verdict
- **Volume spike in the sweep/confirm window (V3: GC volume >= 1.5x its 20-bar average on the signal candle or any of the 6 before it) PASSED on both models that had enough trades,** and it was the half-A pick for both.
  - momentum_rsi + V3: 524 trades, +0.281R (A +0.159 / B +0.392) vs ungated 616, +0.254R (A +0.120 / B +0.372). Max DD 20.1R vs 24.2R. **STRONG** (t >= 2.5). But total R is lower (147R vs 156R): the gain is per-trade quality and drawdown, not more profit.
  - smc_ob + V3: 219 trades, +0.254R (A +0.104 / B +0.383) vs ungated 270, +0.182R (A +0.018 / B +0.328). Max DD 20.8R vs 22.9R. **PASS, not strong.** Total R is higher (55.6R vs 49.2R) with 51 fewer trades.
- **CVD divergence (D1/D2) and absorption (A1/A2) did not help.** They mostly hurt, which is the ICT "delta divergence at the sweep" idea failing again, now with real futures volume (proxy delta).
- session_break_retest: the best gate (A1) had 75 trades, below the 100-trade bar. No pass.
- session_sweep_evening: 15 trades ungated. It can't be judged.

## Luck check
32 gated tests. Passes: momentum_rsi V1, V3; smc_ob V3, V4. That's 4 passes, all from the same family (volume spike), on 2 independent models, with the same gate picked on half A for both. That's more than the 1-2 expected by chance, and it's coherent. Still, the improvements over ungated are modest (+0.02 to +0.09R per trade).

## Next step (proposal, not run)
Run V3 one time, unchanged, on COMEX 2010-2022 (GC prices + GC volume, research/long_history_study.py style) as out-of-sample confirmation. Only if it holds there: propose adding V3 to smc_ob first (it gains both R and drawdown), with Ricardo's approval.

## Full table
| model | gate | n | win% | expR | totR | A_exp | B_exp | maxDD_R | PASS | STRONG |
|---|---|---|---|---|---|---|---|---|---|---|
| momentum_rsi | V1 | 303 | 43.2 | 0.337 | 102.2 | 0.246 | 0.405 | 15.6 | ✓ | ✓ |
| momentum_rsi | **V3 (pick)** | 524 | 40.5 | 0.281 | 147.2 | 0.159 | 0.392 | 20.1 | ✓ | ✓ |
| momentum_rsi | ungated | 616 | 40.4 | 0.254 | 156.3 | 0.120 | 0.372 | 24.2 | | |
| momentum_rsi | V4 | 382 | 38.2 | 0.165 | 63.1 | 0.150 | 0.181 | 24.0 | | |
| momentum_rsi | D2 | 113 | 38.9 | 0.121 | 13.7 | -0.249 | 0.395 | 21.1 | | |
| momentum_rsi | V2 | 177 | 37.9 | 0.093 | 16.5 | 0.185 | 0.016 | 15.0 | | |
| momentum_rsi | D1 | 205 | 35.6 | 0.065 | 13.3 | -0.210 | 0.293 | 39.3 | | |
| momentum_rsi | A1 | 321 | 34.9 | 0.032 | 10.3 | -0.127 | 0.190 | 32.9 | | |
| momentum_rsi | A2 | 126 | 33.3 | -0.049 | -6.2 | -0.077 | -0.026 | 23.6 | | |
| smc_ob | V2 | 60 | 33.3 | 0.412 | 24.7 | 0.302 | 0.490 | 7.1 | | |
| smc_ob | A2 | 69 | 34.8 | 0.297 | 20.5 | 0.268 | 0.327 | 9.6 | | |
| smc_ob | **V3 (pick)** | 219 | 36.5 | 0.254 | 55.6 | 0.104 | 0.383 | 20.8 | ✓ | |
| smc_ob | V4 | 171 | 35.7 | 0.216 | 36.9 | 0.092 | 0.332 | 18.4 | ✓ | |
| smc_ob | A1 | 152 | 36.2 | 0.214 | 32.5 | 0.087 | 0.317 | 17.2 | | |
| smc_ob | V1 | 111 | 33.3 | 0.203 | 22.6 | 0.068 | 0.303 | 13.5 | | |
| smc_ob | ungated | 270 | 35.9 | 0.182 | 49.2 | 0.018 | 0.328 | 22.9 | | |
| smc_ob | D1 | 94 | 33.0 | 0.128 | 12.0 | 0.090 | 0.163 | 12.5 | | |
| smc_ob | D2 | 37 | 16.2 | -0.376 | -13.9 | -0.200 | -0.562 | 15.4 | | |
| session_break_retest | D1 | 8 | 50.0 | 1.162 | 9.3 | 1.494 | 0.167 | 2.1 | | |
| session_break_retest | D2 | 6 | 50.0 | 1.147 | 6.9 | 1.637 | 0.167 | 1.1 | | |
| session_break_retest | A2 | 7 | 42.9 | 0.831 | 5.8 | 1.097 | 0.167 | 2.1 | | |
| session_break_retest | A1 (pick) | 75 | 34.7 | 0.495 | 37.1 | 0.478 | 0.514 | 13.3 | | |
| session_break_retest | V3 | 96 | 34.4 | 0.408 | 39.2 | 0.350 | 0.470 | 16.6 | | |
| session_break_retest | ungated | 108 | 34.3 | 0.342 | 36.9 | 0.377 | 0.307 | 16.5 | | |
| session_break_retest | V4 | 83 | 33.7 | 0.320 | 26.6 | 0.108 | 0.571 | 14.4 | | |
| session_break_retest | V1 | 52 | 30.8 | -0.017 | -0.9 | -0.371 | 0.395 | 10.4 | | |
| session_break_retest | V2 | 36 | 27.8 | -0.110 | -4.0 | -0.202 | -0.007 | 9.4 | | |
| session_sweep_evening | ungated | 15 | 66.7 | 0.451 | 6.8 | -0.069 | 0.640 | 2.1 | | |
| session_sweep_evening | (all gates) | 1-4 | | | | | | | | |

## Out-of-sample check: COMEX GC 2010-06 -> 2022-12 (run 2026-11-17)

`research/orderflow_oos_study.py`, run once, unchanged, on the server. GC prices and GC volume, live
settings and exits, 08:00-11:00 NY, costs in R as every study. Pass bar fixed before running (see script docstring).

**Verdict: FAIL for both models.** V3 failed condition 1 (expectancy must be > 0). It passed conditions 2 and 3.

| model | gate | n | expR | totR | maxDD_R | t |
|---|---|---|---|---|---|---|
| smc_ob | ungated | 584 | -0.133 | -77.8 | 110.3 | -1.95 |
| smc_ob | V3 | 502 | -0.080 | -40.3 | 86.7 | -1.06 |
| momentum_rsi | ungated | 1774 | -0.183 | -325.2 | 325.2 | -4.71 |
| momentum_rsi | V3 | 1518 | -0.155 | -235.4 | 235.4 | -3.61 |

Expectancy (R) by period:

| model | gate | 2010-12 | 2013-15 | 2016-18 | 2019-22 |
|---|---|---|---|---|---|
| smc_ob | ungated | 0.052 | 0.087 | -0.318 | -0.264 |
| smc_ob | V3 | 0.139 | 0.184 | -0.296 | -0.245 |
| momentum_rsi | ungated | -0.193 | -0.074 | -0.246 | -0.196 |
| momentum_rsi | V3 | -0.176 | -0.054 | -0.177 | -0.183 |

What it means:
- V3 improved expectancy in 4/4 periods for both models and cut drawdown 21-28%. The filter effect is consistent.
- But the models themselves lose money on 2010-2022 GC, gated or not. A filter on a losing model is still a losing model, so V3 does not go forward as a proposal.
- Bigger finding: smc_ob and momentum_rsi have no edge before 2023 on this data (momentum_rsi t = -4.7). Caveats: GC futures prices, not XAUUSD; XAUUSD.R costs applied in R; back-adjusted continuous contract. Worth checking the live lineup on research/long_history_study.py's 16-year run before trusting 2023+ results.
