# Atlas master desk

A private dashboard built around Ricardo's own way of trading (rule book: `/mnt/project-files/atlas/rick-rules.md`).

**It is a chart read, not a proven signal.** Over 16 years of COMEX gold, the daily Fib + 1m extreme-RSI model lost
-98R and all his pieces stacked lost -129R after costs. The desk shows where his rules line up and scores every read.

## What is on it
- Markets: gold, silver, oil, S&P 500, US30, Nasdaq 100, dollar index, 10Y T-Note (`config/desk.yaml`; first symbol found on the broker is used, or the Databento live feed).
- Bias (4H + daily), daily Fibonacci leg with golden ratio 0.618-0.65 and the demand/supply zone, solid 4H candle out of the zone, retest level.
- RSI(14) on 1m / 5m / 15m / 1H / 4H with his bands: buy 16.5-20, sell 80-85.
- FVGs and order blocks on 5m / 15m, session levels (PDH/PDL, Asia, London, NY, midnight open), structure (BOS / CHoCH) per timeframe.
- His 6-point checklist and a plain-words desk read. SMT divergence pairs.
- Red-folder USD news (free Forex Factory feed, the same file the bot's blackouts read): live per-second countdown per event, and each event's bot windows (no new entries / flat / back on).
- Dollar & yields vs gold: synthetic DXY and 10Y yields (via ZN), today's move, return correlation with gold on 15m and 1H, and a tailwind / headwind read. Gold vs DXY and gold vs ZN are also in the SMT panel. Context only, not a tested edge.
- Bot: running / stopped, balance, today's P&L, open trades (bot and manual), today's closed trades, every model with its record.
- Desk reads scorecard: a read is logged when 5 of 6 checks line up; scored 2 x ATR(15m) target vs 1 x ATR stop.
- His record and rules (manual account findings, guardrails, 16-year test results).

## Controls and guardrails
The page can always make the bot safer: switch a model off, pause all new entries, emergency stop (kill switch, type STOP).
It has no risk-size control. Guardrails (`config/desk.yaml`). Ricardo 2026-11-18: "just a cool down, don't stop trading":
- cool-down after a loss: the bot's own `risk.cooldown_after_loss_minutes` (30 min, commit 46d4297). The desk reads that
  number from the bot config, shows the bot's live countdown (`cooldown_until` in its risk state), and blocks switching
  anything back on from the page until it ends;
- day lockouts (-X% on the day, N losses in a row) exist but are OFF (0). Setting them turns new entries off for the rest of the NY day.
The bot reads `state/desk_controls.json` before every NEW entry (`atlas_trader/desk/controls.py`). Open trades are always managed.
A missing or broken file has no effect, so the desk can never stop the bot from managing trades.
Note: the lockout is tripped by the desk process; if the desk is not running, the bot's own risk limits still apply.

## Running it
```bash
atlas desk --demo                 # made-up prices, no broker, password "demo" unless DESK_PASSWORD is set
atlas desk                        # real: separate READ-ONLY TradeLocker login, needs DESK_PASSWORD in .env
```
On the server: `deploy/atlas-desk.service` (127.0.0.1:8787, Nice 10, 300 MB memory cap).
Prices come from the TradeLocker feed through one slow throttle (1 call per second at most) so the desk cannot eat the bot's rate limits.

## Live CME prices (Databento)

With `desk.databento.enabled: true` in `config/desk.yaml` and `DATABENTO_API_KEY` in `.env`, the desk
draws gold, silver, oil, S&P, US30 and Nasdaq from CME futures (continuous front month: GC, SI, CL, ES,
YM, NQ) on Rick's live Databento subscription:

* At start it downloads 1-minute bars (4 days) and 1-hour bars (240 days) once. The cost is checked
  first and the download is skipped above `max_history_usd` (that market then stays on broker prices).
* Then one live session on 1-second bars keeps the forming candle current. The page refreshes every
  5 seconds while the feed is live (15 s otherwise). If the session drops it reconnects and replays the
  gap (Databento keeps 24 h for replay).
* 4H and daily candles start at 18:00 New York, the CME trading day.
* Futures sit a little apart from the broker's CFD prices (the basis). The page shows the broker price
  next to the futures price, and moves the bot's trade-entry lines by the basis so they sit where the
  trade really is. The bot itself still trades and reads broker prices only; nothing here changes it.
* The dollar index is not on CME (DX trades on ICE). It is built from the six CME currency futures (6E 6J 6B 6C SEK 6S) with the ICE weights; futures carry puts the level slightly off spot DXY, moves and swings track it. If the krona leg has no data, `dollar_fallback.sek` is used and the panel says so. If history for the legs would push the cost over `max_history_usd`, the main markets still load and the legs fill from the live replay.
* 10Y yields: CME 10-year T-Note (ZN.v.0). ZN up = yields down. Dollar index and ZN have `reads: false`: no desk reads are logged for them.

### Background desk reads and broker load (2026-11-22)

* Every 60 s the desk checks all markets on the live feed for a desk read and scores open ones, even
  with the page closed (`desk.background_reads_s`). These checks make no broker calls.
* Broker calls from the desk: account and positions at most every 30 s, the broker price (for the
  basis) at most once a minute per market on screen, and a failed call waits a full cache period
  before it is tried again. The bot's TradeLocker rate limit comes first.
* On weekends Databento only replays the last ~20 h; when it rejects the replay start, the desk
  restarts from the earliest time it allows.
