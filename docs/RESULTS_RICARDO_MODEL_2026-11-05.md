# Ricardo's model (break → retest, trailing stop) on 1-minute gold, 2026-11-05

Code: `atlas_trader/strategies/break_retest.py`, trailing stop in `atlas_trader/backtest.py`,
study `research/ricardo_research.py`. Branch `research-trend`.

## Rules tested
5m break-of-structure direction → 1m consolidation → solid breakout candle closing beyond it
(BOS, optionally CHoCH) → verified retest that holds → entry. The stop goes beyond the retest wick. Once
the trade is up `trail_start_r` × risk, each pullback candle followed by a strong candle moves the stop to just
beyond the pullback wick. One position at a time, with no adding.

## Data and protocol
GatesFX XAUUSD.R 1-minute bars, 2025-10-01 to 2026-11-05 (388,922 bars), split into halves at
2026-04-10. Costs are the same as all other studies. There are 32 pre-registered configs: bias (5m BOS / none) × CHoCH (yes/no) ×
trail start (0.5R / 1R) × trail room (0.3 / 0.6 ATR) × target (trail only / 2R).

## Result
| | Value |
|---|---|
| Configs passing | **0 of 32** |
| Win rate | 19%–32% (median 27%) |
| Expectancy | −0.16R to −0.31R per trade (median −0.24R) |
| Trades per config | 406–1,524 |
| Losing in both halves | 32 of 32 |
| Average stop moves per trade | 0.13–0.83 (most trades stop out before the trail starts) |

Every config lost money in both halves, with t-stats from −2.6 to −5.7. That is a consistent loss, not noise.
Estimate: with 1-minute stops of a few dollars, spread + slippage + commission (≈ $0.41/oz round trip)
cost roughly 0.15–0.25R per trade. So even a break-even entry signal loses at this timeframe.

## What the trade videos show (Drive "Atlas Trades", 4 of 5 viewed; one still processing)
- Aug 16, ~8:36 PM NY, TopstepX 150K Express, MGC 1m. Short 15 → added 15 (30 total, avg 4,446.3) after
  a fast rally rolled over and broke its rising trendline (CHoCH). Stop tightened to 4,446.8 (≈ $150 risk on
  30), target 4,439. Closed manually ≈ +$541 within ~5 min.
- Jul 17, ~8:48 AM NY, 50K Express, MGC 15m. Short 30 @ 3,985.2 into a large drop. Stop already trailed to
  3,979.4 (locks ≈ +$1,725). Open profit ≈ +$3.9–4.2k.
- Jul 6, ~9:31 PM NY, 50K Express, MGC 1m. Long 7 @ 4,145.7 after a sharp sell-off reversed with strong
  green candles. Resistance lines at 4,151.2 / 4,161.7 / 4,170.5, target 4,185.6. No stop visible in the
  sampled frames.
- Mar 9 (chat upload, CFD account): 16 stacked buys, 0.28 lots, +$712.

The videos show reversal entries after fast moves (not consolidation breakouts), size added once
the stop protects the position, and discretionary early exits. The early exits are likely where much of the 68%
comes from, and they are not codified here.

## Verdict
Do not put `break_retest` on the live bot. The codified version of these rules loses money on 1-minute
gold after costs.
