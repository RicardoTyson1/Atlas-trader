# P4 Bitcoin weekends, 2026-11-07: nothing passes, so Bitcoin stays off the bot

- **Scripts:** `research/btc_weekend_data.py` (step 1, data) and `research/btc_weekend_study.py` (steps 2–3).
- **Tests:** `tests/test_weekend.py` (window edges, DST) and `tests/test_btc_weekend.py` (Sunday 18:00 close with no look-ahead, entries only in the window, the spread and cost filters, bid/ask fills).
- **Run:** in `/opt/atlas-research` only. Nothing in `/opt/atlas-trader`, `config/default.yaml` or `state/` was changed. The broker was only read from: no orders were placed.

## Step 1: data

**Bars:** GatesFX BTCUSD 1-minute bars, 2023-10-23 20:02 to 2026-11-07 22:41 UTC.

- 1,583,107 bars, of which 451,231 fall inside weekend windows.
- 158 complete weekends.
- **The broker has more history.** Bars go back past 2021. The download was capped at about 3 years because the 1 GB server, shared with the live bots, can't hold more in memory.

**Gaps:**

- 3,472 gaps longer than 1 minute; 77 longer than 30 minutes.
- Most of the long gaps are weekend maintenance stops, usually Saturday or Sunday mornings UTC. Examples: 2026-03-07 (8h23m), 2026-02-07 (6h49m), 2024-01-06 (6h48m), 2026-04-25 (6h46m), 2024-07-07 (5h01m).
- The longest was 2026-02-21 08:03 to 2026-02-22 09:55 UTC: 25h52m, most of that weekend.
- One weekday gap: 2025-07-11 20:34 to 2025-07-12 09:17 (12h43m).
- The study handles gaps by filling a stop at the next open after a gap, and closing at the first bar at or after Sunday 18:00.

**Spread:** TradeLocker's history download returns ONE price series, with no bid/ask and no price-type option, so historical spread can't be downloaded.

- **The bars are bid prices.** In the last seconds of each test candle, the candle's close sat at the bid (median position in the spread −0.003, where 0 = bid and 1 = ask).
- **Live weekend spread sample** (Saturday 2026-11-07, 17:55–18:00 NY, 60 quotes):

| Median | 90th percentile | Max | Min |
|---|---|---|---|
| **$67.02** | **$67.54** | **$70.23** | $67.02 |

The sample matches Ricardo's $67 screenshot from Nov 6. As agreed, the study prices every trade at the 90th-percentile spread, **$67.54**, plus $5 slippage per side, with no commission. Because the spread is fixed, the $80 spread filter never triggered. The cost filter then requires 1R ≥ 5 × $67.54 = **$337.70**.

## Step 2: rules (as given)

- **Window:** Friday 17:00 to Sunday 18:00 New York time. Every trade closes on the bar ending Sunday 18:00.
- **Filters:** skip if the spread is above $80, or if the first target is under 5 × spread. These models have no fixed target, so the first target is +1R, where the trail starts.
- **Models:** smc_ob 5m, st_confluence 15m, momentum_rsi 5m, all with their gold rules (ATR-scaled), trailing from 1R. Indicators run on the continuous 24/7 series.
- **Fills:** longs buy the ask and sell the bid; shorts the reverse. Stops trigger on the price you would exit at.
- **Risk:** 2.5% per trade (half of gold's 5%). All results are in R.
- **Halves:** 158 weekends, 2023-10-27 to 2026-10-30. Half B starts with the weekend of 2025-05-02.

## Step 3: results

Max drawdown is in R, with trades in time order.

| Model | Half | Trades | Win % | Expectancy R | Total R | Max DD R | t | Label |
|---|---|---|---|---|---|---|---|---|
| smc_ob | A | 12 | 41.7 | −0.343 | −4.1 | 5.2 | −1.37 | promising, not proven |
| smc_ob | B | 20 | 35.0 | −0.332 | −6.6 | 9.8 | −1.48 | promising, not proven |
| st_confluence | A | 22 | 27.3 | −0.422 | −9.3 | 11.0 | −2.19 | promising, not proven |
| st_confluence | B | 18 | 22.2 | −0.243 | −4.4 | 6.8 | −1.09 | promising, not proven |
| **momentum_rsi** | **A** | 30 | 40.0 | **+0.140** | **+4.2** | 12.8 | 0.34 | promising, not proven |
| momentum_rsi | B | 54 | 20.4 | **−0.450** | **−24.3** | **25.0** | **−3.24** | promising, not proven |

Every row has fewer than 100 trades, so all are labelled "promising, not proven" as the pass bar requires; none of them is actually promising.

**Pass bar:** the model picked on half A was momentum_rsi (+4.2R). On half B:

- **Expectancy:** −0.450R (fail).
- **Trades:** 54 (ok).
- **Max drawdown:** 25.0R against a limit of 20R (fail).

**Result: NOT PASSED.**

## Verdict

**Nothing passes, so Bitcoin weekend trading stays off the bot.**

- **Costs are the main problem.** A $67.54 spread plus $10 slippage per round trip is large next to weekend BTC moves on 5–15m charts. The cost filter allows only setups with a stop at least $338 away, and those few trades still lose after costs.
- **smc_ob and st_confluence lost in both halves.**
- **momentum_rsi failed out of sample.** Its small half-A gain (+4.2R over 30 trades, t 0.34) turned into −24.3R on half B (t −3.24), with a 25R drawdown that breaks the 20R limit.

**Caveats:**

- **The spread is fixed.** If weekend spreads have often been wider than the $67.54 sampled today, real results would be worse, not better.
- **Rules end here.** Per the pre-registration, there is no retesting with new settings.

**Reproduce (in `/opt/atlas-research`):**

```
PYTHONPATH=. python research/btc_weekend_data.py --env /opt/atlas-trader/.env   # download + spread sample
PYTHONPATH=. python research/btc_weekend_study.py data/raw/BTCUSD_5m.csv
```
