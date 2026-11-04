# Atlas Trader: Claude in Chrome setup SOP (DEMO deployment)

Goal: Claude in Chrome collects your real rules and specs, puts them in the
bot's config, rents a small cloud server, installs Atlas Trader on it, and starts it
running 24/7 on your TradeLocker **DEMO** account, with Telegram alerts.

## Before you start, log in yourself to:
1. Your prop firm dashboard
2. TradeLocker web (the **DEMO** account)
3. GitHub (github.com/RicardoTyson1/Atlas-trader)
4. DigitalOcean (cloud.digitalocean.com), with a payment method already added
5. Telegram Web (web.telegram.org)

Claude never types your passwords. Three steps are marked **[YOU]**; Claude will
stop and hand those to you.

Then paste everything in the box below into Claude in Chrome.

---

```
You are setting up my automated trading bot "Atlas Trader" so it runs 24/7 on a
cloud server against my TradeLocker DEMO account. I am logged in to my prop firm
dashboard, TradeLocker (demo), GitHub, DigitalOcean, and Telegram Web.
Work through the phases in order. After each phase, give me a short summary
before you continue.

HARD RULES. Follow them at all times:
- Never place, modify, or close a trade or order. Never click Buy or Sell.
- DEMO ONLY. Never use the word "live" in any command, never use the flag
  --i-accept-real-money-risk, and never set "mode: live" in any file.
- Never type, reveal, copy, or store any password, API key, bot token, or 2FA code.
  When a step needs one, STOP and tell me exactly where to type it myself.
- Before ANY action that costs money (creating a server), STOP and ask me to confirm.
- Do not change account, risk, or platform settings on the prop firm or TradeLocker.
- Write numbers exactly as shown on the page. Never guess. Write NOT FOUND if missing.
- If a command prints an error, stop, copy the exact error text into your summary,
  and ask me. Do not improvise fixes.

PHASE 1: PROP FIRM RULES (prop firm dashboard / rules or FAQ page)
For MY current account/plan, record: firm, plan, phase, account size, profit target
(% and $), daily loss limit (% and $) and HOW it is measured (start-of-day balance
or equity, initial balance, floating equity?), max drawdown (% and $; static or
trailing; trails balance or equity; does it lock?), min trading days, time limit,
news rule (blackout minutes), overnight/weekend rule, consistency rule, max lots,
and the EXACT wording of any rule about EAs, bots, algorithmic, or copy trading.
-> If automated/bot trading is PROHIBITED on this account, STOP HERE and tell me.

PHASE 2: TRADELOCKER SPECS (TradeLocker web, DEMO account)
Confirm the account is DEMO (if not, STOP). Record the SERVER name (as on the
login screen) and the account number. For NAS100, US500, US30 (record the exact
symbol names your broker uses), open the instrument info and record: contract
size / value per 1.0 point per 1.0 lot, min lot, lot step, max lot, current
spread (with time), commission, session hours.

PHASE 3: PUT MY NUMBERS INTO THE BOT CONFIG (GitHub web)
1. Open https://github.com/RicardoTyson1/Atlas-trader/blob/main/config/default.yaml
   and click the pencil (Edit) icon.
2. Under "account:", set starting_balance to my account size.
3. Under "prop_rules:", set: firm, phase, profit_target_pct, daily_loss_pct,
   daily_loss_basis (initial OR start_of_day), max_drawdown_pct,
   drawdown_type (static OR trailing), trailing_on (balance OR equity),
   trail_locks_at_initial (true/false), min_trading_days.
   Leave safety_buffer at 0.70.
4. Under "instruments:", set value_per_point, min_lot, lot_step, spread_points and
   commission_per_lot for each symbol. If my broker's symbol names differ (for
   example "NAS100.cash"), rename the keys AND the names in the "strategies:" list
   to match exactly.
5. Leave "mode: backtest" as is. Do not touch anything else.
6. Commit directly to main with the message "Config: real prop rules and instrument specs".
7. Show me a before/after list of every value you changed.

PHASE 4: TELEGRAM ALERTS (Telegram Web)
1. Open a chat with @BotFather, send /newbot, name it "Atlas Trader Alerts", and give
   it a unique username ending in "bot" (e.g. atlas_ricardo_alerts_bot).
2. BotFather will show a token. DO NOT copy it or repeat it. Tell me: [YOU] "Your bot
   token is in the BotFather chat. You will paste it in Phase 6."
3. Open a chat with @userinfobot, send /start, and record the numeric "Id" it replies with
   (this is my TELEGRAM_CHAT_ID, which is not secret).
4. Open my new bot's chat and press Start, so the bot is allowed to message me.

PHASE 5: CREATE THE SERVER (DigitalOcean)
1. Create -> Droplets. Region: New York. Image: Ubuntu 24.04 LTS.
   Size: Basic, Regular, the cheapest plan (about $6/month). Authentication: Password.
2. [YOU] STOP and ask me to (a) confirm the monthly cost and (b) type the root
   password into the password box myself.
3. Hostname: atlas-trader. After I confirm, click Create Droplet.
4. When it is ready, record the droplet's public IPv4 address, then open it and click
   "Console" (Droplet Console / Recovery Console) to get a terminal.

PHASE 6: INSTALL THE BOT (in the Droplet Console)
Type each block below exactly, press Enter, and wait for it to finish before the
next one. Report anything that looks like an error.

  apt update && DEBIAN_FRONTEND=noninteractive apt -y upgrade
  apt -y install python3-venv python3-pip git nano
  adduser --disabled-password --gecos "" atlas
  mkdir -p /opt/atlas-trader && chown atlas:atlas /opt/atlas-trader
  sudo -u atlas git clone https://github.com/RicardoTyson1/Atlas-trader.git /opt/atlas-trader

  (If git asks for a username/password, the repo is private. STOP and tell me.)

  cd /opt/atlas-trader
  sudo -u atlas python3 -m venv .venv
  sudo -u atlas .venv/bin/pip install -e ".[tradelocker,dev]"
  sudo -u atlas .venv/bin/pytest -q

  (The last line must end with "passed" and no "failed". If not, STOP.)

  sudo -u atlas cp .env.example .env && chmod 600 .env

Now STOP and tell me: [YOU] "Run this in the console:
  sudo -u atlas nano /opt/atlas-trader/.env
and fill in:
  TL_EMAIL, TL_PASSWORD   = your TradeLocker DEMO login
  TL_SERVER               = <the server name from Phase 2>
  TL_ACCOUNT_ID           = <the demo account number from Phase 2, e.g. 2594911>
  WEBHOOK_SECRET          = any long random text you make up
  TELEGRAM_BOT_TOKEN      = the token from the BotFather chat
  TELEGRAM_CHAT_ID        = <the Id from Phase 4>
Save with Ctrl+O, Enter, then exit with Ctrl+X. Tell me when done."
Do not look at or read back the contents of .env.

PHASE 7: CONNECTION TEST (after I say the .env is done)
  cd /opt/atlas-trader
  sudo -u atlas .venv/bin/python -c "from atlas_trader.cli import _load_env; _load_env(); from atlas_trader.brokers.tradelocker import TradeLockerBroker as T; b=T('https://demo.tradelocker.com', allowed_account_ids=[2594911]); print('ACCOUNT', b.account()); print(b.bars('NAS100','5m',3))"

(Replace NAS100 with the exact symbol name from Phase 2 if different.)
Expected: a line starting with ACCOUNT showing balance/equity, then 3 rows of prices.
If you see an error, STOP and copy it to me exactly. Do not continue.

PHASE 8: RUN IT 24/7 (DEMO)
  cp /opt/atlas-trader/deploy/atlas.service /etc/systemd/system/atlas.service
  systemctl daemon-reload
  systemctl enable --now atlas
  sleep 20; systemctl status atlas --no-pager
  journalctl -u atlas -n 40 --no-pager

Expected: status "active (running)", and the log contains "Atlas started in DEMO mode".
Check Telegram: my bot should have sent an "Atlas started in DEMO mode" message.

PHASE 9: FINAL REPORT
Produce exactly this:

=== ATLAS SETUP REPORT ===
Date:
PROP FIRM: firm / plan / phase / size:
  target | daily loss (+how measured) | max DD (+static/trailing/trails on/lock):
  min days | news | overnight | consistency | max lots:
  bot/EA rule (exact words):
TRADELOCKER DEMO: server | account number:
  per symbol: name | value/point/lot | min lot | step | max lot | spread | commission | hours
CONFIG CHANGES COMMITTED: (before -> after list) + commit URL
TELEGRAM: bot username | chat id | start message received? (yes/no)
SERVER: provider | region | size | monthly cost | IPv4
INSTALL: tests result line | connection test output (balance + 3 bars, no secrets)
SERVICE: systemctl status line | last 10 log lines (remove anything secret)
PROBLEMS / QUESTIONS:
=== END ===
```

---

## After Claude in Chrome finishes

Paste the report into your Claude Code session.

Useful commands in the Droplet Console:

| What | Command |
|---|---|
| See if it's running | `systemctl status atlas --no-pager` |
| Live log | `journalctl -u atlas -f` (Ctrl+C to exit) |
| Risk status | `cd /opt/atlas-trader && sudo -u atlas .venv/bin/atlas status` |
| **Emergency stop** (flatten + stop) | `sudo -u atlas touch /opt/atlas-trader/state/KILL` |
| Stop the service | `systemctl stop atlas` |
| Update to newest code | `cd /opt/atlas-trader && sudo -u atlas git pull && systemctl restart atlas` |

This runs on **DEMO only**. Going live comes later, and only if the backtests and at
least 4 weeks of demo results hold up.
