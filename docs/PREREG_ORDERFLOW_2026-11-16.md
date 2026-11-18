# Order-flow gate study: pre-registration (draft for Ricardo's OK, 2026-11-16)

**Question.** Does real COMEX gold volume (GC, from Databento) improve the bot's sweep-type entries on XAUUSD? This is the one part of the ICT/sweep idea that was never tested, because GatesFX data has no real volume.

**Not financial advice. Everything below is fixed BEFORE running. No changes after seeing results.**

## Data
- Entries, prices, and costs: GatesFX `XAUUSD.R_5m.csv`, 2023-03 to 2026-11, halves A/B (same as every study since Nov 4). Real spread and slippage, plus $7/lot per side.
- Volume: `GC_5m.csv` (Databento GLBX.MDP3, GC front month by volume, back-adjusted, from `research/databento_gc.py`). Joined to XAUUSD bars by timestamp (UTC). A bar without GC data counts as "gate fails" (conservative).
- Holdout 2023-25 one-shot rule unchanged: only a PASS goes there, and only once.

## Models (the live lineup, live settings, no re-tuning)
1. session_sweep evening (5m)
2. session_sweep morning (15m)
3. smc_ob (5m)
4. momentum_rsi (5m)

reversal_trail (1m) is excluded because there's no 1m GC join in v1.

## Gates (8, fixed)
For each entry, look at the **sweep / signal candle** (session_sweep: the candle that swept the level; smc_ob / momentum_rsi: the signal candle).

| # | Gate | Rule |
|---|---|---|
| V1 | Volume spike 1.5× | GC volume of that candle ≥ 1.5 × average of the previous 20 candles |
| V2 | Volume spike 2.0× | same, 2.0× |
| V3 | Volume spike 1.5×, sweep OR confirm candle | either candle passes V1 |
| V4 | Volume spike 2.0×, sweep OR confirm candle | either candle passes V2 |
| D1 | Delta divergence | proxy bar delta = volume × sign(close − open), summed as CVD. For a short after a high sweep: price makes a new 20-bar high but CVD does not (long is the mirror) |
| D2 | D1 + V1 | both |
| A1 | Absorption | sweep candle volume ≥ 1.5× avg AND body ≤ 35% of range (big volume, small move) |
| A2 | A1 + D1 | both |

Proxy delta is a known approximation (no aggressor side in 1m OHLCV). True CVD from Databento `trades` is a v2 study, and only if the cost check is under the cap and a gate here shows promise.

## Exits
Each model's live exit only (trail from 1R; session_sweep uses its live partial settings). No exit grid, to keep the test count small.

## Pass bar (per model × gate, 4 × 8 = 32 tests)
- Expectancy > 0 after costs in BOTH halves, AND ≥ 100 trades total.
- Beats the **ungated** model's expectancy in both halves (otherwise the gate adds nothing).
- Max drawdown in R no more than 20% worse than ungated.
- Pick on half A only (best total R per model), then that one pick must pass on half B.
- STRONG = also t ≥ 2.5.
- With 32 tests, about 1–2 "passes" are expected by luck alone. A single marginal pass is reported as "lead, not proven".

## Outputs
`docs/RESULTS_ORDERFLOW_<date>.md`: trades, win rate, expectancy, total R, drawdown, t-stat, per half, per model × gate, plus how many entries each gate removes. Failures reported plainly.

## Code (research only, no config change)
- `atlas_trader/orderflow.py`: GC join, volume ratio, proxy CVD, gates. Includes a no-look-ahead test (gates only use completed candles).
- `research/orderflow_gate_study.py`: the grid above.
- `tests/test_orderflow.py`

Run in /opt/atlas-research:
`PYTHONPATH=.:research nice -n 19 python research/orderflow_gate_study.py data/raw/XAUUSD.R_5m.csv data/raw/GC_5m.csv`
