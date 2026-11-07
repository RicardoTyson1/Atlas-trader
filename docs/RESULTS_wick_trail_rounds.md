# momentum_rsi wick trail: bounded retest (3 rounds + holdout), 2026-11-07. Nothing passed; the live trail stays

- **Script:** `research/wick_trail_rounds.py`, with the rounds, pass bar and holdout fixed before running.
- **Data:** development on the same 13 months, entries and costs as `docs/RESULTS_wick_trail.md`.
- **Trail:** 5m candles only, no break-even step.
- **Pass bar:** the same as the first study. Each round picks the best total R on half A; that pick must then beat the baseline's half-B total R, have expectancy > 0, and keep its max drawdown within 1.2× the baseline's.
- **Holdout:** the first round to pass would have gone to a one-shot test on untouched 5m data from 2023-03 to 2025-09. No round passed, so the holdout was never used and stays untouched for future ideas.
- **Run:** in `/opt/atlas-research` only.

**Baseline (live "trail from 1R"):**

| Half A R | Half B R | Half B max DD R |
|---|---|---|
| +17.9 | **+24.2** | **13.5** |

## Round 1: start the wick trail later (activation 1.5R or 2.0R)

| Variant | Half A R | Half B R | B max DD R | B paired t |
|---|---|---|---|---|
| act 1.5R, buffer 0.10 | +20.5 | +19.2 | 16.4 | −1.05 |
| act 1.5R, buffer 0.25 | +23.7 | +17.8 | 17.1 | −1.34 |
| act 1.5R, buffer 0.50 | +21.6 | +19.8 | 17.2 | −0.72 |
| act 2.0R, buffer 0.10 | +22.8 | +17.9 | 16.6 | −0.95 |
| **act 2.0R, buffer 0.25 (pick)** | **+26.2** | +17.1 | 16.9 | −1.07 |
| act 2.0R, buffer 0.50 | +24.2 | +19.9 | 16.3 | −0.56 |

**NOT PASSED:** half B +17.1R against +24.2R, and max drawdown 16.9R against a limit of 16.2R.

## Round 2: with activation 2.0R, confirmation on a close above S's close and/or a looser strong candle (body ≥ 50% of range, ≥ 0.3 ATR)

| Variant | Half A R | Half B R | B max DD R | B paired t |
|---|---|---|---|---|
| confirm high, loose, buffer 0.10 | +15.3 | +15.4 | 16.6 | −1.20 |
| confirm high, loose, buffer 0.25 | +20.0 | +14.8 | 16.9 | −1.29 |
| confirm high, loose, buffer 0.50 | +22.0 | +17.8 | 16.3 | −0.77 |
| confirm close, buffer 0.10 | +20.3 | +17.9 | 16.6 | −0.96 |
| **confirm close, buffer 0.25 (pick)** | **+23.6** | +17.1 | 16.9 | −1.08 |
| confirm close, buffer 0.50 | +21.7 | +20.6 | 16.3 | −0.47 |
| confirm close, loose, buffer 0.10 | +13.5 | +18.5 | 16.6 | −0.86 |
| confirm close, loose, buffer 0.25 | +13.1 | +17.8 | 16.9 | −0.97 |
| confirm close, loose, buffer 0.50 | +19.5 | +20.6 | 16.3 | −0.47 |

**NOT PASSED:** half B +17.1R against +24.2R, and max drawdown 16.9R against 16.2R.

## Round 3: with round 2's settings, any pullback counts (no lower low needed)

| Variant | Half A R | Half B R | B max DD R | B paired t |
|---|---|---|---|---|
| no-dip, buffer 0.10 | +21.1 | +14.0 | 16.6 | −1.34 |
| **no-dip, buffer 0.25 (pick)** | **+24.1** | +13.1 | 16.9 | −1.45 |
| no-dip, buffer 0.50 | +22.0 | +16.4 | 16.3 | −0.89 |

**NOT PASSED:** half B +13.1R against +24.2R, and max drawdown 16.9R against 16.2R.

## Verdict

**All 18 adjusted wick trails lost to the current trail on half B,** making +13.1 to +20.6R against +24.2R. **All of them also had a deeper half-B drawdown,** 16.3 to 17.2R against 13.5R.

- **The pattern held across every version.** Over 3 rounds and 18 variants, the wick trail "wins" the earlier half and loses the later half by about the same amount. That is what curve-fitting looks like, not an edge.
- **What we stop doing:** further tweaks to this exit on momentum_rsi. The live candle trail stays, and nothing is deployed.
- **The holdout is still clean,** so it remains available for a genuinely new idea.

**Reproduce:**

```
PYTHONPATH=. python research/wick_trail_rounds.py data/raw/XAUUSD.R_1m.csv data/raw/XAUUSD.R_5m.csv
```
