# Multi-market edge hunt: results (2026-11-19)

**Verdict: nothing good enough for the desk or the bot.** 1 of 35 pre-registered tests passed, and only weakly (NQ overnight hold, t = 1.76, not STRONG). With 35 tests, 1-2 weak passes are expected by luck alone.

Data: Databento CME 1-minute bars, Jun 2010 to Nov 2026, GC, SI, CL, ES, YM, NQ (cost $0). Rules fixed before running: `docs/PREREG_MULTI_MARKET_2026-11-19.md` (branch `research/multi-market`). Costs on every trade: GC $1.26 (the bot's real gold cost), others 2 ticks per side + $5.
Pass = 200+ trades, profitable after costs in both halves (split June 2018) and in 4 of 6 periods. STRONG = also t >= 2.5.

## Scoreboard (R = multiples of risk, after costs)
| Family | Test | Trades | Win % | R / trade | Before costs | Total R | Verdict |
|---|---|---|---|---|---|---|---|
| Trend 1h (Donchian 55/20) | GC | 1,456 | 28 | -0.031 | +0.134 | -44.5 | FAIL |
| | SI | 1,461 | 26 | -0.038 | +0.077 | -55.2 | FAIL |
| | CL | 1,590 | 28 | -0.014 | +0.053 | -22.5 | FAIL |
| | ES | 1,641 | 28 | -0.123 | -0.016 | -202.6 | FAIL |
| | YM | 1,644 | 27 | -0.001 | +0.060 | -1.4 | FAIL |
| | NQ | 1,625 | 29 | +0.025 | +0.072 | +41.1 | FAIL (2nd half -3.2R) |
| | All 6 together | 9,417 | 28 | -0.030 | +0.062 | -285.1 | FAIL |
| SMT sweep reversal 5m | ES/NQ NY | 1,971 | 34 | -0.261 | -0.077 | -515.1 | FAIL |
| | ES/NQ Asia | 345 | 37 | -0.111 | +0.082 | -38.2 | FAIL |
| | ES/YM NY | 1,623 | 32 | -0.289 | -0.081 | -468.4 | FAIL |
| | ES/YM Asia | 323 | 38 | -0.164 | +0.070 | -52.8 | FAIL |
| | NQ/YM NY | 2,565 | 33 | -0.246 | -0.068 | -631.5 | FAIL |
| | NQ/YM Asia | 571 | 39 | -0.090 | +0.090 | -51.6 | FAIL |
| | GC/SI NY | 1,775 | 35 | -0.242 | -0.007 | -429.7 | FAIL |
| | GC/SI Asia | 504 | 35 | -0.234 | 0.000 | -117.8 | FAIL |
| Lead-lag 5m (30 min hold) | ES->GC | 7,706 | 24 | -0.320 | +0.001 | -2,462.5 | FAIL |
| | ES->CL | 7,268 | 34 | -0.256 | +0.014 | -1,863.7 | FAIL |
| | ES->SI | 7,829 | 28 | -0.274 | +0.029 | -2,146.9 | FAIL |
| | GC->SI | 1,058 | 28 | -0.308 | -0.023 | -325.6 | FAIL |
| | SI->GC | 1,660 | 26 | -0.310 | +0.008 | -515.3 | FAIL |
| | ES->NQ | 266 | 36 | +0.025 | +0.217 | +6.7 | FAIL (3/6 periods) |
| | NQ->ES | 667 | 34 | -0.227 | +0.085 | -151.7 | FAIL |
| | ES->YM | 322 | 31 | -0.244 | -0.082 | -78.5 | FAIL |
| Overnight hold (16:00 -> 09:30 NY, long) | GC | 4,079 | 47 | -0.037 | +0.023 | -149.5 | FAIL |
| | SI | 4,053 | 48 | -0.027 | +0.014 | -108.5 | FAIL |
| | CL | 4,072 | 49 | -0.020 | +0.003 | -81.4 | FAIL |
| | ES | 4,082 | 51 | -0.018 | +0.021 | -75.0 | FAIL |
| | YM | 4,086 | 51 | +0.002 | +0.024 | +9.6 | FAIL |
| | **NQ** | **4,082** | **54** | **+0.011** | +0.027 | **+45.5** | **PASS (weak, t 1.76)** |
| Turn of month (long) | GC / SI / CL / ES / YM / NQ | 197 each | 49-57 | -0.05 to +0.07 | | -10.5 to +13.8 | all FAIL (under 200 trades) |

## What it means
1. **SMT is a loser, even before costs**, in every NY-killzone pair (-0.07 to -0.08R per trade gross). When one index sweeps a range and its partner doesn't, price tends to *keep going* in the sweep's direction, not reverse. That's the opposite of the textbook read.
2. **Lead-lag is efficient.** A shock in ES tells you nothing about gold, oil or silver over the next 30 minutes (gross ~0R). Costs turn it into a big loser.
3. **Trend following has a small raw edge in metals, oil and Nasdaq (+0.05 to +0.13R before costs), but costs eat it.** Gold's live Donchian model is the one that comes closest. Its edge before costs is +0.134R per trade, but costs of ~0.165R per trade make it negative under this futures cost model. It only stays positive if real costs come in lower than $1.26 per trade, which supports cutting its risk before real money.
4. **NQ overnight drift is real but tiny** (+0.011R per trade, about +45R over 16 years). It's a known academic effect, too small to matter for a trader like Rick.

## Order-flow data price quotes (1 year, Nov 2025 to Nov 2026, from Databento, not bought)
| Schema | GC | SI | CL | ES | YM | NQ | All 6 |
|---|---|---|---|---|---|---|---|
| trades (every trade, aggressor side) | $1.79 | $0.54 | $0.91 | $7.03 | $0.93 | $5.53 | ~$16.7 |
| tbbo (each trade + top of book) | $2.98 | $0.91 | $1.51 | $11.72 | $1.55 | $9.22 | ~$27.9 |
| mbp-1 | $2.54 | $1.53 | $1.87 | $14.25 | $4.32 | $16.72 | ~$41.2 |
| mbp-10 (10-level book) | $164.89 | $112.17 | $101.76 | $470.72 | $146.51 | $768.22 | ~$1,764 |
