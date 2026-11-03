# Atlas Trader

A rule-based intraday trading system for index CFDs (NAS100, US500, US30) on
**TradeLocker**. It's built around **prop-firm survival** first and profit second.

## Read this first

- **No system wins 99.99% of the time.** Profitable systematic traders often
  win 35–60% of trades. They make money because their winners are bigger than
  their losers and they never take a loss that ends the account.
- **The strategies here are not yet proven on real data.** The included
  strategies are standard, well-known starting points. Until you've backtested
  them on real data and run them on demo, assume they don't work.
- **A prop account dies from one rule breach, not from a bad week.** That's why
  the risk engine stops the bot at 70% of each firm limit (configurable). It
  should never touch the real limit.

## What it does

| Piece | File | What it's for |
|---|---|---|
| Risk engine | `atlas_trader/risk.py` | Sizes every trade, enforces the daily-loss, trailing-drawdown, trades-per-day, losing-streak, session and news rules. Every order goes through it. |
| Strategies | `atlas_trader/strategies/` | Opening Range Breakout, Trend Pullback and VWAP Mean Reversion. |
| Backtester | `atlas_trader/backtest.py` | Conservative fills, spread, slippage and gaps; the stop is assumed hit first when a bar touches both stop and target. Uses the same risk engine as live. |
| Out-of-sample and sweeps | `atlas backtest --oos`, `atlas sweep` | Separates real edge from curve-fitting. |
| Monte Carlo | `atlas_trader/montecarlo.py` | Reshuffles your backtest days to estimate the odds of passing the challenge vs. breaching. |
| Live runner | `atlas_trader/runner.py` | Runs 24/7. Uses completed bars only and attaches a broker-side SL/TP to every order. Flat every day by 15:50 ET. State survives restarts. |
| TradeLocker | `atlas_trader/brokers/tradelocker.py` | Official SDK. Demo by default. Live trading needs two separate opt-ins. |
| TradingView alerts | `atlas_trader/webhook.py` | Your own TradingView setups can trigger trades, still through the risk engine. |
| Kill switch | `state/KILL` | Create this file and the bot closes everything and stops. |
| Alerts | `notify.py` | Telegram and/or Discord messages for every entry, exit and halt. |

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tradelocker,dev]"
cp .env.example .env        # then fill it in. Never commit .env
pytest                       # 30 tests should pass
```

## Workflow

**Step 1: set your real numbers.** Edit `config/default.yaml`. Every line
marked `VERIFY` matters:
- Toptraders' exact daily-loss %, max drawdown %, whether the drawdown trails
  (on balance or equity), and the profit target. Look these up on their rules
  page or ask support.
- `value_per_point`, `min_lot` and `lot_step` for each symbol. In TradeLocker,
  open the instrument's info and check its contract size. If these are wrong,
  every position size is wrong.

**Step 2: get real data.** Export NAS100 at 5m from TradingView (Export chart
data). It needs to be the GatesFX/TradeLocker feed if you can get it, since CFD
prices differ slightly from futures. More history is better; aim for at least a
year.

**Step 3: test honestly.**
```bash
atlas compare  --data data/NAS100_5m.csv --tf 5m
atlas backtest --data data/NAS100_5m.csv --strategy orb --oos 0.3 --mc
atlas sweep    --data data/NAS100_5m.csv --strategy orb --grid "reward_risk=1.5,2,3;range_minutes=5,15,30"
```
Only keep a strategy if **out-of-sample** profit factor is above about 1.2,
expectancy is positive after costs, there are at least 100 trades, and the
Monte Carlo pass rate is well above its fail rate. If none qualifies, don't
trade any of them yet. That outcome is the system working.

**Step 4: dry-run the live loop** on historical data (no network, no money):
```bash
atlas simulate --data data/NAS100_5m.csv
```

**Step 5: run on a TradeLocker DEMO account for 4+ weeks.**
```bash
atlas run --mode demo --webhook
```
Compare demo fills and results with what the backtest predicted. Big
differences mean the backtest assumptions (spread, slippage) are wrong.

**Step 6: live, only after steps 3–5 hold up.** Set `mode: live` in the config
and run:
```bash
atlas run --mode live --i-accept-real-money-risk
```

## TradingView alerts

Run with `--webhook` on a server with a public address. In the TradingView
alert, set the Webhook URL to `http://YOUR_SERVER:8787/tv` and the message to:
```json
{"secret": "YOUR_WEBHOOK_SECRET", "symbol": "NAS100", "action": "buy", "stop": {{low}}, "rr": 2}
```
`action` can be `buy`, `sell` or `close`. The bot sizes the position, checks
every rule and attaches the SL/TP. A visit to `GET /` returns the current risk
status.

## Running 24/7

Use a small Linux VPS (around $5–10 a month) with `deploy/atlas.service`. The
broker holds the stop-loss, so if the server dies, open trades stay protected.

## Safety switches

- `touch state/KILL`: flatten everything and stop.
- `atlas status`: see floors, today's trades and any halts.
- A **hard halt** (bot drawdown buffer reached) stays until you review it and
  run `atlas reset-halt`. A real-limit breach can't be reset by the bot.
- After 5 errors in a row, the bot closes all positions and stops.

## Roadmap

1. Paid intraday history (e.g. Databento or Polygon for futures, as a proxy for
   CFD index data), so tests cover years of data, not weeks.
2. Walk-forward optimisation (rolling re-fit, then test on the next unseen
   window).
3. Regime filter (trend vs. chop, volatility) to switch strategies on and off.
4. Economic calendar feed that fills `news_blackouts` automatically.
5. Portfolio mode: several strategies sharing one risk budget.
6. Dashboard (trades, equity and halts) inside Atlas-os.

*Nothing here is financial advice. Trading leveraged products can lose more
than you expect, quickly.*
