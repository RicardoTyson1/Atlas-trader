# Trader's model (liquidity sweep → FVG) + leak-plugging rules, 2026-11-04

## 1. Leaks found in the GatesFX trade history (Jan–Aug 2026, 977 trades)
Personal data stays local; summary only.
- Net trading result -$34.4k. Commission was 20% of that.
- Win rate 68%, but average loss was 2.6× the average win (losers held 3× longer).
- Positions closed on their own: +$22.4k. Stacked/added-to positions: -$56.7k.
- One stacked day (10 lots, no stop) lost $37.2k, more than the whole net loss.
- Re-entry 0.7 min after a loss (vs 4.6 after a win). Median 22 trades/day, up to 74.
- Up to 4 lots on ~$2k (+ bonus credit). A $5 move could wipe the account.

## 2. Rules added (config/default.yaml → risk, prop_rules, session)
| Leak | Rule |
|---|---|
| Adding to losers | one position at a time; guardian closes any add-on |
| No stop | guardian closes any position without a stop-loss |
| Oversizing / bonus leverage | 0.5% risk per trade, max 0.5 lots, size from ≤ $10k real money |
| Revenge re-entry | 15-min cooldown after any losing trade (manual trades too) |
| Overtrading | max 8 trades/day, done after 3 losses in a row |
| Blow-up days | done for the day at -2%; bot stops at -10% total |
Replaying the actual history with no-adding + 0.20-lot cap + -$200 day stop:
-$34.4k → about -$0.7k.

Live-verified on demo #2594911: a 0.01-lot trade with no stop was closed by the
guardian within one cycle; bracket orders are detected as protected.

## 3. The trader's model, codified (`liquidity_fvg`)
Daily fib premium/discount bias → killzone sweep of PDH/PDL or Asian range →
displacement FVG → retrace entry, stop beyond the sweep, target opposite liquidity.
`research/smc_research.py`: 81 pre-registered variants.
- 0 of 81 met the DEV bar (all positive-in-both-halves variants had t < 1.5, too few trades).
- 30/81 variants positive on DEV, median -0.04R. Discount bias was the best filter
  (median +0.02R); trend bias clearly worse (-0.16R).

## 4. Full bot (model + rules, real lot sizes, $10k)
| Period | Trades | Result | Worst day | Worst trade |
|---|---|---|---|---|
| DEV Nov 2024–Mar 2026 | 99 | +$1,056 (+10.6%) | -$49 | -$49 |
| HOLDOUT Mar–Nov 2026 | 42 | -$680 (-6.8%) | -$48 | -$48 |
| All 2 years | 145 | +$218 (+2.2%) | -$49 | -$49 |

**Verdict:** the protection works (worst day -$49 vs -$37,168 in real trading).
The model has no proven edge. It goes to DEMO forward-testing only.
