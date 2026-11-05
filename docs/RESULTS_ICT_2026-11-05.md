# ICT (Inner Circle Trader) research, 2026-11-05

## Sources and status of the method
- Rules codified from public ICT model descriptions (Silver Bullet, 2022 Mentorship
  Model, Judas swing / Power of 3, SMT divergence, OTE). ICT's own YouTube videos are
  not machine-readable from here; third-party write-ups disagree on details (entry at
  FVG edge vs 50% CE, stop placement), so both entry variants were tested.
- No audited long-run ICT track record exists; sources report he did not place in the
  2024 Robbins World Cup (not verified against official results).
- Best independent study found (StatOasis, daily SPY/QQQ/DIA/IWM 1993-2026): no ICT
  concept showed a statistically significant edge; none beat buy-and-hold. It did not
  test intraday killzone trading, which is what this study adds.

## Protocol (`research/ict_research.py`, fixed before running)
- DEV (selection): 2024-11 → 2026-11. FRESH (never seen): 2022-11 → 2024-11
  (gold from 2023-03). GatesFX 5m data, real spreads, slippage and $7/lot fees.
- 224 pre-registered (config × market) runs: gold, NAS100, US30, SPX500.
- Limit-order entries at the FVG / OTE level, conservative fills.

## Results
| Model | DEV: share of configs profitable | Notes |
|---|---|---|
| Silver Bullet, gold | **22/24** | the only consistent family |
| Silver Bullet, indices | 8/72 | |
| 2022 Model | 10/48 | SPX500 0/12 |
| Judas swing / PO3 | 11/32 | |
| SMT divergence (indices) | 5/12 | |
| OTE | **0/36** | negative on every market |

Only 1 of 224 runs met the full DEV bar (Silver Bullet, gold, all 3 windows,
discount bias, edge entry, 3R). On FRESH it **failed**: 107 trades, -0.136R, t -0.96.
The earlier `liquidity_fvg` model also **failed** on FRESH: 137 trades, -0.065R.

Secondary look (all 24 Silver Bullet gold configs on FRESH, done after the primary
test): 9/24 positive, median -0.08R, near-zero correlation with DEV results. One
sub-group held up in both periods: **the 10-11 AM NY window with no bias filter.
All 6 such configs were positive in DEV and FRESH** (FRESH +0.10R to +0.35R per
trade, 25-67 trades, best t 1.84). The discount-bias versions are the ones that broke.

## Verdict
No ICT model passed the pre-registered test. The 10-11 AM Silver Bullet on gold
is the one lead worth a live demo forward-test, but it is post-hoc, built on small
samples, and NOT statistically proven. Treat it as a hypothesis.
