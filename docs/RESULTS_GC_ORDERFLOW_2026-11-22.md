# Gold order-flow study (Databento TBBO) — results, 2026-11-22

Data: GC front month, every trade with aggressor side plus top of book, 2025-11-02 to 2026-10-30 (70,339 bars of 5 minutes). Cost: $3.86.
Rules were pre-registered in `PREREG_GC_ORDERFLOW_2026-11-22.md`. The cost is 1.26 per contract round trip, the minimum stop is $5, and the target is 2R.
Pass bar for one year: 200+ trades, positive in both halves (split at 2026-05-01), positive in 4 of 6 two-month periods, and positive in the hold-out (2026-08+).

| Test | Trades | Win% | R/trade (net) | R/trade (gross) | Total R | Half A | Half B | +Periods | t | Hold-out R | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|
| O1 delta divergence | 2537 | 33.7 | -0.292 | -0.064 | -740.7 | -323.4 | -417.3 | 0/6 | -11.42 | -197.5 | FAIL |
| O2 delta thrust | 1795 | 34.7 | -0.213 | -0.015 | -381.8 | -143.3 | -238.5 | 0/6 | -6.80 | -138.0 | FAIL |
| O3 absorption | 372 | 40.6 | -0.105 | +0.072 | -39.2 | -34.6 | -4.6 | 2/6 | -1.60 | +4.8 | FAIL |
| O4 block print (>=114 lots) | 211 | 43.1 | -0.067 | +0.110 | -14.2 | -1.3 | -12.9 | 4/6 | -0.80 | -18.1 | FAIL |
| O5 book imbalance (abs > 0.30) | 233 | 40.3 | -0.146 | +0.057 | -34.1 | -25.1 | -9.0 | 2/6 | -2.37 | +0.9 | FAIL |

## Read
- Delta divergence and delta thrust (the order-flow "tells" most commonly taught) lose money even before costs. Fading or following aggressive delta on 5-minute gold has no edge.
- Block prints, absorption and book imbalance show a small gross edge (+0.06 to +0.11R) that costs more than wipe out. They also happen rarely: book imbalance beyond 0.30 occurs on only 0.7% of bars.
- Nothing qualifies for the prior-year out-of-sample purchase. Nothing goes to the desk or the bot.
- With this study the hunt stands at 41 pre-registered Databento tests and 1 weak pass (NQ overnight long, t 1.76).
