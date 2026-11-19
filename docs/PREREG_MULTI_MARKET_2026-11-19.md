# Multi-market edge hunt: pre-registration (2026-11-19)

Fixed BEFORE running. One run, no re-tuning after seeing results. Code: `research/multi_market_study.py`.
Data: Databento GLBX.MDP3 ohlcv-1m, continuous front month by volume (`.v.0`), 2010-06-06 to 2026-11-18,
resampled to 5m and 1h and back-adjusted at rolls (`research/databento_multi.py`). Times in New York.

## Costs (round trip, price units, charged on every trade)
GC 1.26 (the bot's measured real XAUUSD cost). SI, CL, ES, YM, NQ: 2 ticks slippage per side + $5 commission.

## Tests (35)
| Family | Rule | Tests |
|---|---|---|
| D Donchian 1h | close beyond 55-bar high/low -> enter next open; stop 2 ATR(14); exit on close beyond 20-bar opposite channel | 6 markets + portfolio |
| S SMT 5m | Pairs ES/NQ, ES/YM, NQ/YM, GC/SI. NY: reference range 00:00-08:00, trade 08:00-11:00. Asia: reference 08:00-17:00, trade 19:00-22:00. One market sweeps its reference high (low) while the other has not; within 6 bars the sweeper closes back inside -> fade on the sweeper at next open. Stop 1 tick beyond the window extreme; skip if risk < 3x cost. Target 2R; time exit at window end. One trade per pair per window per day. | 8 |
| L Lead-lag 5m | Leader 5m return z > 3 (z vs prior 1000-bar std) while follower |z| < 1 -> follower in leader's direction at next open; stop max(1 ATR(14,5m), 3x cost); exit after 6 bars (30 min). Pairs ES->GC, ES->CL, ES->SI, GC->SI, SI->GC, ES->NQ, NQ->ES, ES->YM. Skips 16:00-18:00 and gaps. | 8 |
| C Overnight | Long 16:00 -> 09:30 NY every night; stop 1 daily ATR(14) | 6 |
| C Turn of month | Long close of 2nd-last trading day -> close of 3rd trading day; stop 2 daily ATR | 6 (all < 200 trades by design, reported for information) |

Stop is checked before target when both are touched in one bar (conservative).

## Pass bar (same as the library study)
- 200+ trades, net R > 0 in BOTH halves (split 2018-06-01) and in at least 4 of 6 equal time periods.
- STRONG = pass and t >= 2.5. 2023+ R is shown as a holdout view only.
- With 35 tests, 1-2 marginal passes are expected by luck. A single non-STRONG pass is a lead, not proof.
- Sanity check done before the real run: on random-walk data every test fails with gross R ~ 0 (no look-ahead).

## What happens with a pass
Report to Rick. Nothing goes on the live bot without his OK. A pass may be shown on the desk as a read.
Order-book (MBP) / trades-based tests come after, on 1-2 years, only after a cost quote Rick approves.
