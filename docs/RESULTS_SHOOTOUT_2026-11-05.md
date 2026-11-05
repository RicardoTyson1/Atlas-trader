# Strategy shoot-out, 2026-11-05: strong vs weak

Script: `research/shootout.py` (44 pre-registered configs). Data: GatesFX XAUUSD.R 1m, 2025-10-01 to
2026-11-05, split at 2026-04-10. Entries 08:00-11:00 NY. Costs as in every study. STRONG = positive in
both halves, at least 100 trades and t ≥ 2.5.

## By model
| Model | Configs | STRONG | Median win% | Median exp R | Best t |
|---|---|---|---|---|---|
| momentum_rsi (RSI crosses 50 on an aggressive candle, trail) | 18 | 0 | 38.6 | **+0.068** | 1.58 |
| reversal_trail (fast move + extreme RSI → CHoCH, 50%-of-move target, trail) | 18 | 0 | **66.2** | **+0.083** | 1.15 |
| break_retest (consolidation → breakout → retest) | 8 | 0 | 20.3 | −0.317 | −0.61 |

## Best configs
| Rank | Model / settings | Trades | Win% | Exp R | t | Half A | Half B |
|---|---|---|---|---|---|---|---|
| 1 | momentum_rsi 5m, disp 1.0 ATR, EMA-50 trend, trail from 1R | 241 | 38.2 | +0.226 | 1.58 | +0.217 | +0.236 |
| 2 | momentum_rsi 5m, same, trail from 0.5R | 245 | 37.6 | +0.185 | 1.34 | +0.186 | +0.183 |
| 3 | momentum_rsi 5m, same, big-candle lock off | 239 | 35.6 | +0.209 | 1.28 | +0.164 | +0.253 |
| 4 | reversal_trail 1m, move 5 ATR, RSI ≤25/≥75, 50%-of-move target | 121 | **68.6** | +0.082 | 1.15 | −0.001 | +0.157 |
| 6 | reversal_trail 1m, move 3 ATR, RSI 25, 50%-of-move target | 108 | 68.5 | +0.083 | 1.07 | −0.049 | +0.206 |
| 9–11 | reversal_trail 5m, 50%-of-move target | 23–60 | 80–83 | +0.06–0.11 | ~0.9 | + | + |

## Verdict
- **Eliminated:** break_retest, which lost in all 8 configs. Earlier studies also eliminated ICT 2022 (`liquidity_fvg`), Silver Bullet,
  ORB, VWAP, trend pullback, session breakouts and channel trend.
- **Kept on the demo account (best config of each):** momentum_rsi #1 and reversal_trail #4.
- These are the first of Ricardo's models with **positive median results** and configs positive in both halves.
  None reaches the t ≥ 2.5 bar yet; the sample is too small to rule out luck. The demo forward test decides.
- The shoot-out tested 08:00-11:00 NY only. The bot now trades 24/5 (daily break 16:50-18:05), so other hours are
  untested until demo results come in.

## Forward-test rule for the demo (fixed now)
After **100 bot trades or 8 weeks**, whichever comes first: a model stays only if its demo expectancy is
> 0 after costs, and its worst day stayed inside the day stop. Otherwise it is eliminated.
