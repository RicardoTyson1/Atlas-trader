# Three "high win-rate" archetypes on gold, 2026-11-06

Script: `research/archetypes_study.py` (52 pre-registered configs). Data: GatesFX XAUUSD.R 5m,
2023-03-02 to 2026-11-05 (3.7 years), halves A (to 2025-01-02) and B (from 2025-01-02). 15m was resampled from 5m.
Real spread, slippage and $7/lot. PASS = expectancy > 0 in both halves and at least 100 trades. STRONG = PASS and t ≥ 2.5.

| Model | Configs | Passed | Strong | Median win% | Median exp R | Best t |
|---|---|---|---|---|---|---|
| bb_reversion (fade the 2-3σ Bollinger close back to the middle band) | 24 | **0** | 0 | 35.9 | −0.133 | −0.54 |
| smc_ob (H4 bias → sweep → displacement FVG → order-block retest) | 12 | **1** | 0 | 36.0 | −0.035 | 1.56 |
| st_confluence (SuperTrend + MACD + volume, pullback entry) | 16 | **1** | 0 | 39.5 | −0.014 | 0.60 |

## Deployed to the demo bot (the passing config of each model)
| Model | Settings | Trades | Win% | Exp R | t | Half A | Half B |
|---|---|---|---|---|---|---|---|
| smc_ob | 5m, sweep 24 candles, trail only, 08-11 NY | 270 | 37.0 | +0.209 | 1.56 | +0.066 | +0.336 |
| st_confluence | 15m, SuperTrend ×3, no ATR filter, 08-11 NY | 113 | 42.5 | +0.054 | 0.46 | +0.069 | +0.038 |

## Not installed
- **bb_reversion:** all 24 versions lost money. Mean reversion to the middle band doesn't pay on gold once
  costs are included and the target has to be at least 1R.
- **All-hours versions of every model** failed. The 08-11 NY window again holds the edge, as in earlier studies.

## Caveats
- Neither deployed model is STRONG. smc_ob is the better candidate (t 1.56, mostly from 2025-26).
  st_confluence barely passes (+0.05R per trade, t 0.46) and is the first one to cut if the demo disagrees.
- None of these setups is "high win-rate" on gold: win rates were 35-42%. Profit comes from winners being
  bigger than losers, through the trailing stop.
- Demo rule (fixed now): after 50 bot trades per model, or 6 weeks, a model stays only if its demo
  expectancy after costs is > 0.
