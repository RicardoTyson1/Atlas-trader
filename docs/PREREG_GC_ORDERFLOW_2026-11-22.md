# Gold true order-flow study: pre-registration (2026-11-22)

Rick chose "Gold only (~$3)". Fixed BEFORE buying data or seeing results. One run, no re-tuning.

## Data
Databento GLBX.MDP3 `tbbo` (every trade with aggressor side + best bid/ask sizes), GC.v.0 front month,
2025-11-01 to 2026-11-01. Cost checked first; hard cap $5 (`research/gc_orderflow_build.py`).
Built into 1m order-flow bars, roll-adjusted, then 5m bars (`research/gc_orderflow_study.py`).
True delta = buyer-aggressor volume minus seller-aggressor volume. (The earlier CVD test used a price-based proxy.)

## Tests (5, both directions each)
| # | Name | Signal on a closed 5m bar | Stop | Hold |
|---|---|---|---|---|
| O1 | Delta divergence | new 20-bar high (low) while last-3-bar true delta < 0 (> 0) -> fade | beyond the 3-bar extreme | 12 bars |
| O2 | Delta thrust | delta z > 2.5 vs prior 100 bars, bar closes the same way -> follow | 1 ATR(14) | 12 bars |
| O3 | Absorption | volume z > 2.5 at a new 20-bar low (high), delta against the bar, close in upper (lower) half -> reverse | beyond the bar | 12 bars |
| O4 | Block print | largest single trade >= 99.5th pct of month 1 (month 1 excluded from trading) -> follow its side | 1 ATR(14) | 6 bars |
| O5 | Book imbalance | mean top-of-book imbalance > +0.30 (< -0.30) -> follow | 1 ATR(14) | 3 bars |
All: entry next bar open, stop at least $5 away, target 2R, one trade at a time, $1.26 cost per trade.
Stop checked before target in the same bar.

## Pass bar (1-year version of the usual bar)
200+ trades; net R > 0 in both halves (split 2026-05-01) and in >= 4 of 6 two-month periods. STRONG = also t >= 2.5.
5 tests -> a single weak pass is a lead, not proof.

## If something passes
Re-run the exact same rules on the prior year (2024-11 to 2025-11) as out-of-sample, after a price quote and Rick's OK.
Only a pass on both years is proposed for the desk (as a read/alert, not bot trading) and only with Rick's OK.

Sanity check before the real run: on random synthetic order flow every test fails with gross R ~ 0.
