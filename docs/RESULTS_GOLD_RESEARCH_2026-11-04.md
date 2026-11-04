# Gold strategy research, 2026-11-04

Script: `research/gold_research.py` (protocol in its docstring, fixed before running).
Data: GatesFX XAUUSD.R 5m, 2024-11-04 → 2026-11-04.
DEV = 2024-11-04 → 2026-03-24 (gold bull run, 2,736 → 4,547, peak 5,598).
HOLDOUT = 2026-03-25 → 2026-11-04 (gold fell 4,498 → 3,948, buy & hold -13.5%).
Costs: 0.14 spread + 0.10 slippage + $7/lot fee. Edge measured in R per trade.

## Pre-registered families (70 configs)
| Family | Idea | Timeframe | Configs |
|---|---|---|---|
| london_breakout | Asian range (00:00–02/03:00 NY), break before 08:00 | 5m | 24 |
| comex_open_breakout | Range from 08:20 NY COMEX open, break before 11:00 | 5m | 18 |
| donchian_1h | Channel breakout trend following, multi-day | 1H | 16 |
| donchian_4h | Same, slower | 4H | 8 |
| evening_long_drift | Long at 19:00/20:00 NY, hold 3–9h | 1H | 6 |

## Result
13 of 70 configs passed the DEV bar (positive in both DEV halves, t ≥ 1.5, ≥ 60 trades).
The best of each family was then evaluated ONCE on the holdout:

| Family | Config | Holdout trades | Exp R | t | Verdict |
|---|---|---|---|---|---|
| donchian_1h | entry 55, exit 20, stop 2 ATR | 58 | -0.036 | -0.15 | FAIL |
| comex_open_breakout | range 08:20–08:50, mid stop, 3R | 153 | -0.137 | -1.11 | FAIL |
| evening_long_drift | long 20:00, hold 3h | 127 | -0.061 | -1.34 | FAIL |

**No strategy passed.** Everything that looked good during the bull run stopped
working when the market changed. These are textbook "it worked in the backtest" setups
that would have lost money if traded from March 2026 onward.

The holdout has now been used. Any further idea must be tested on new data
(e.g. demo forward-testing from today) or the holdout result is no longer clean.
