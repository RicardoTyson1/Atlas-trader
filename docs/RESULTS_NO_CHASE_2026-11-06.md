# Bad entries fixed: no-chase filter, 08-11 only, min reward:risk (2026-11-06)

## What went wrong live (demo #2594911, Thu 2026-11-05)
| Entry (NY) | Trade | Why it was bad | Result |
|---|---|---|---|
| 09:35 BUY 0.38 | momentum_rsi | bought after a 2.7-ATR spike candle and a 4.1-ATR run, at the stock-market open | stopped in 3 min, **-$456** |
| 10:15 SELL 0.17 | momentum_rsi | normal 1.1-ATR candle, 2.8-ATR run | **+$271** |
| 12:48 BUY 0.18 | TradingView alert | | -$90 |
| 14:45 BUY 0.06 | momentum_rsi (off-hours) | 2.2-ATR candle after a 2.7-ATR run | -$86 |
| 18:30 BUY 0.07 | momentum_rsi (off-hours) | 2.2-ATR candle after a 4.2-ATR run | +$35 |
| 20:36 BUY 0.05 | TradingView alert | target $2.78 away, stop $18 away: **0.15R** | open at time of writing |

## Study (`research/no_chase_study.py`, protocol fixed before running)
GatesFX XAUUSD.R 5m, 2023-03 → 2026-11 (3.7 years), halves A (2023-03 → 2024-12) and
B (2025-01 → 2026-11), live momentum_rsi settings, real costs, R per trade.

**Off-hours entries:** lost in every filter setting. No filter: -260.9R (A), -29.2R (B).
**No-chase filter (08-11):** candle <= 2.5 ATR and 1-hour run <= 3 ATR was adopted (the
largest worst-half gain among the filters that improved both halves):

| 08-11 NY | A trades | A total | A per trade | B trades | B total | B per trade | B t |
|---|---|---|---|---|---|---|---|
| no filter (old) | 431 | +15.7R | +0.036R | 443 | +75.9R | +0.171R | 1.81 |
| **no-chase** | 290 | **+42.6R** | **+0.147R** | 325 | **+119.7R** | **+0.368R** | **2.95** |

Replaying 2026-11-05 with the new settings: the old config fired 10 signals; the new one
fires only the 10:15 SELL (the +$271 winner).

## Changes
- `momentum_rsi`: optional no-chase filter (`max_candle_atr`, `max_run_atr`, `run_bars`).
- `config/default.yaml`: entries 08:00-11:00 NY only (was all hours); no-chase 2.5 / 3.0.
- Runner: `risk.min_target_r` (1.0): never take a trade whose target pays less than 1x the
  risk at the ACTUAL fill price (would have blocked the 20:36 trade).

Still a screen, not proof: half A is +0.147R/trade with t 1.25. The demo decides.
