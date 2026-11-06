# 60-second scalper study (2026-11-06)

Ricardo's theory: unlimited trades, each closed within a minute. `research/scalp_study.py`,
protocol fixed before running. GatesFX XAUUSD.R 1m, 2024-06 → 2026-11 (864,135 candles),
halves A (→ 2025-08) / B. Entry at the next 1m open, exit at stop / target / that candle's
close (≤ ~60 s), stop 1 x ATR(1m). Live costs: spread 0.14 + slippage 0.10/side + $7/lot.

## Result: 0 of 44 configs pass (expectancy > 0 in both halves, ≥ 100 trades/half)
- **Costs take a median 0.23R per trade.** Before costs, most rules hover around zero
  (momentum, breakout, continuation slightly negative; fades slightly positive). After
  costs every high-frequency rule loses: e.g. fading 1.5-ATR candles at all hours
  = 10,186 trades in half B at -0.14R; buying 15-candle breakouts = -0.33R to -0.47R.
- At 1% risk per trade, -0.14R x 10,000 trades is -1,400% of the account: certain ruin.
- The only rule positive after costs in both halves: **fade a 3-ATR 1-minute candle, 08-11
  NY, time exit** (+0.168R / +0.194R per trade, t 0.94 / 1.53). It fails the bar on trade
  count: 63 and 93 trades in ~14 months each (about 5 a month), and is not significant.

## Verdict
60-second scalping on GatesFX gold loses to costs. Not deployed. The one near-miss
(3-ATR spike fade) trades rarely, so it is the opposite of "unlimited trades".
