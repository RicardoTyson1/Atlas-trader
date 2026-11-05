# ICT models and slow trend on gold, 2026-11-04

Two pre-registered studies. The rules and pass bars were written before any results were seen.
Code: `research/ict_research.py`, `research/trend_research.py`,
`atlas_trader/strategies/silver_bullet.py`. Branch `research-trend`.

## 1. ICT models on FRESH data

The data is GatesFX XAUUSD.R 5m bars from **2023-03-02 to 2024-11-03** (520 days). No earlier study used this
period; everything from 2024-11-04 on is excluded because it was already used. The period is split into
A (to 2024-01-03) and B (from 2024-01-04). Costs are the same as `gold_research.py`.

| Model | What it codifies | Configs | Positive | Median exp R | Median win% |
|---|---|---|---|---|---|
| ICT 2022 model (`liquidity_fvg`) | Killzone sweep of PDH/PDL/Asian range → displacement FVG → retrace | 81 | 25% | **-0.071** | 30 |
| ICT Silver Bullet | First FVG inside the 03-04 / 10-11 / 14-15 NY window, with HTF bias | 54 | 7% | **-0.217** | 31 |

To pass, a config needed positive expectancy in both halves, at least 100 trades and t ≥ 3.0
(strict, because 135 configs were tested).
**Result: 0 of 135 pass.** The best config had t = 0.75, which is noise. 7 of the 10 top-ranked configs
were negative in half A and positive in half B, a sign of a regime-dependent pattern rather than an edge.

Limits: ICT's YouTube models (Charter PA Models 1-13, Silver Bullet, 2022 model) are largely
discretionary. Public summaries leave stops, OTE levels and "displacement" undefined, so this study tests
the common, codable core of these models, not a trader's judgment.

## 2. Slow trend / time-series momentum (the best-documented gold edge)

The data is GatesFX XAUUSD.R daily bars, 2015-01-26 to 2026-11-05. The out-of-sample period, **2016-01-01 onward**, starts after the
Bartsch et al. (2018) study's sample ended. Positions are volatility-targeted to 10%. Costs are 3bp per unit of turnover plus 4%/yr financing
on the notional held.

| Rule (OOS) | Sharpe | CAGR | Max DD | t | Verdict |
|---|---|---|---|---|---|
| Buy & hold (same sizing/costs) | 0.70 | 7.2% | -20.0% | 2.41 | benchmark |
| MOM3 long/flat | 0.74 | 6.4% | -14.6% | 2.57 | PASS (narrow) |
| MOM1 long/flat | 0.72 | 5.9% | -16.3% | 2.50 | PASS (narrow) |
| MOM24 long/flat | 0.74 | 7.4% | -22.8% | 2.45 | FAIL (DD) |
| MA50/200 long/flat | 0.64 | 5.6% | -18.7% | 2.19 | FAIL |
| Any long/short version | 0.04-0.64 | — | up to -47% | — | FAIL |

**Read:** a long-only trend filter roughly matches holding gold, with about a quarter less drawdown. The
Sharpe gain over buy & hold (+0.02 to +0.04) is too small to call an edge. Shorting gold on trend signals lost money.
This matches the literature: the trend filter helps by stepping aside in long downtrends, not by
beating the market.

## Recommendation
- Do not add Silver Bullet or any ICT variant to the live bot. Keep `liquidity_fvg` on demo forward test only.
- No intraday gold strategy tested so far (300+ configs across 5 studies) has a proven edge after costs.
- The only rule with support is a slow, long-only trend filter. It is a position-holding approach, not
  intraday, and conflicts with the bot's flat-by-15:50 and prop-firm design. Using it would be a
  separate product decision.
- The data periods are now used up. Any new idea must be judged on **demo forward results from 2026-11-05 on**.
