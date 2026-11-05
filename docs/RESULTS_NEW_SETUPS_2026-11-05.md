# New setups and exits tested on 2026-11-05

Data: GatesFX XAUUSD.R 1-minute, 2025-10-01 to 2026-11-05 (NAS100.R 1-minute for the shock
study), split into halves A/B. Costs: real spread, slippage, and $7/lot. Every test had its rules and pass bar fixed
before it ran (scripts in `research/`). Bar to reach the demo: expectancy > 0 in BOTH halves.

| Idea (Ricardo) | Script | Result | Verdict |
|---|---|---|---|
| Take profit at the nearest FVG 50% / liquidity / major S/R ≥ 1R | `tp_study.py` | Lost to the current exits in all 4 tests (e.g. momentum_rsi +0.01R vs +0.23R) | Off (code kept, `tp: none`) |
| Trail from the first profit (0R) instead of +1R | `tp_study.py` | momentum_rsi +34R vs **+55R** at 1R; reversal_trail about the same | Back to 1R |
| Trendline break → retest → rejection (15m / 1h lines, 5m entry) | `trend_study.py` | 8 configs, 18–54 trades each; none positive in both halves (best 1h + BOS + 2R: +0.22R, B −0.04R) | Off |
| Supply / demand zone retest (base → displacement → first return → rejection) | `zone_study.py` | 8 configs, all negative (best 5m: 72 trades, −0.16R) | Off |
| Runaway lock (keep X% of the best open profit) | `runaway_study.py` | momentum_rsi best (1.5R, keep 50%): +58.5R vs +55.1R, but worse in half A; reversal_trail no effect | Off (`exits:` empty) |
| Nasdaq shock: what gold does | `shock_study.py` | Gold FELL during 46 of 61 fast Nasdaq drops of ≥1% (avg −$19/oz) and 17 of 19 drops of ≥1.5% (avg −$32). After the drop, direction is a coin flip (2h: +$0.5, t 0.1). After ≥1.5% drops, gold was +$16 by 16:50 on average (both halves positive, t 1.4, n 19): suggestive, not proven | No trading rule; gold usually falls WITH stocks during the drop |

Live bot after these tests (branch `research-trend`): momentum_rsi + reversal_trail, trail from 1R,
TradingView Silver Bullet alerts, automatic high-impact-news blackouts. Trades are closed at the
16:50 daily break, so multi-day holds (the 1-3 day trendline idea) were tested the same way.
