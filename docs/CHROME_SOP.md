# Atlas Trader: cloud server setup with Claude in Chrome

Goal: rent a small cloud server (DigitalOcean, about $6/month), install Atlas Trader,
and run it 24/7 on your GatesFX TradeLocker **DEMO** account with Telegram alerts.

## Before you start, log in yourself to:
1. DigitalOcean (cloud.digitalocean.com), with a payment method added
2. Telegram Web (web.telegram.org)
3. healthchecks.io (free account; sign up with your email first)

Claude never types your passwords. Steps marked **[YOU]** are yours.
Paste everything in the box below into Claude in Chrome.

---

```
You are setting up my trading bot "Atlas Trader" on a cloud server so it runs 24/7
against my GatesFX TradeLocker DEMO account. I am logged in to DigitalOcean and
Telegram Web. Work through the phases in order and summarize after each one.

HARD RULES. Follow them at all times:
- DEMO ONLY. Never type the word "live" in any command, never use the flag
  --i-accept-real-money-risk, and never edit any file under config/.
- Never type, reveal, copy, or store any password, API key, bot token, or 2FA code.
  When a step needs one, STOP and tell me exactly where to type it myself.
- Before ANY action that costs money (creating the server), STOP and ask me to confirm.
- If a command prints an error, STOP, copy the exact error text, and ask me.

PHASE 1: TELEGRAM ALERTS (Telegram Web)
1. Open a chat with @BotFather, send /newbot, name it "Atlas Trader Alerts", and give it a
   unique username ending in "bot".
2. BotFather shows a token. DO NOT copy or repeat it. Tell me: [YOU] "Your bot token is
   in the BotFather chat. You'll paste it in Phase 3."
3. Open a chat with @userinfobot, send /start, and record the numeric Id it replies with
   (TELEGRAM_CHAT_ID; it is not secret).
4. Open my new bot's chat and press Start.

PHASE 1B: DEAD-MAN'S SWITCH (healthchecks.io)
1. In healthchecks.io, add a check named "Atlas 10k bot".
   Schedule: Simple. Period: 5 minutes. Grace time: 10 minutes. Save.
2. In "Integrations", add Telegram (follow its instructions) and keep Email on.
3. The check shows a "ping URL". DO NOT copy it. Tell me: [YOU] "Your ping URL is on the
   check's page. You'll paste it in Phase 3 as HEALTHCHECK_URL."

PHASE 2: CREATE THE SERVER (DigitalOcean)
1. Create -> Droplets. Region: New York. Image: Ubuntu 24.04 LTS.
   Size: Basic, Regular, the cheapest plan (about $6/month). Authentication: Password.
2. [YOU] STOP and ask me to confirm the monthly cost and type the root password myself.
3. Hostname: atlas-trader. After I confirm, click Create Droplet.
4. When it is ready, record the IPv4 address, open the droplet, and click "Console".

PHASE 3: INSTALL (in the Droplet Console). Type each block exactly and wait for it to finish.

  apt update && DEBIAN_FRONTEND=noninteractive apt -y upgrade
  apt -y install python3-venv python3-pip git nano
  timedatectl set-timezone America/New_York
  adduser --disabled-password --gecos "" atlas
  mkdir -p /opt/atlas-trader && chown atlas:atlas /opt/atlas-trader
  sudo -u atlas git clone https://github.com/RicardoTyson1/Atlas-trader.git /opt/atlas-trader

  (If git asks for a username/password, the repo is private. STOP and tell me.)

  cd /opt/atlas-trader
  sudo -u atlas python3 -m venv .venv
  sudo -u atlas .venv/bin/pip install -e ".[tradelocker,dev]"
  sudo -u atlas .venv/bin/pytest -q

  (The last line must say "passed" with no "failed". If not, STOP.)

  sudo -u atlas cp .env.example .env && chmod 600 .env

STOP and tell me: [YOU] "Run:  sudo -u atlas nano /opt/atlas-trader/.env
and fill in:
  TL_EMAIL / TL_PASSWORD  = your TradeLocker login
  TL_SERVER               = GATESFX
  TELEGRAM_BOT_TOKEN      = the token in the BotFather chat
  TELEGRAM_CHAT_ID        = <the Id from Phase 1>
  HEALTHCHECK_URL         = the ping URL from healthchecks.io
Leave TL_ACCOUNT_ID empty (each bot config picks its own demo account).
Save with Ctrl+O, Enter, then exit with Ctrl+X. Tell me when done."
Never display or read back the .env file.

PHASE 4: CONNECTION TEST (after I say .env is done)
  cd /opt/atlas-trader
  sudo -u atlas .venv/bin/python -c "from atlas_trader.cli import _load_env; _load_env(); from atlas_trader.brokers.tradelocker import TradeLockerBroker as T; b=T('https://demo.tradelocker.com', account_id=2594911, allowed_account_ids=[2594911]); print('ACCOUNT', b.account()); print(b.bars('XAUUSD.R','5m',3))"

Expected: "ACCOUNT Account(balance=..." followed by 3 rows of gold prices. On any error, STOP.

PHASE 5: START IT 24/7
  cp /opt/atlas-trader/deploy/atlas@.service /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now atlas@default
  sleep 30; systemctl status atlas@default --no-pager
  journalctl -u atlas@default -n 20 --no-pager

Expected: "active (running)", and the log contains "Atlas started in DEMO mode".
Telegram: my bot should have sent "Atlas started in DEMO mode".

PHASE 6: REPORT
SERVER: region | size | monthly cost | IPv4
TELEGRAM: bot username | start message received (yes/no)
TESTS: final pytest line
CONNECTION TEST: balance shown + whether 3 price rows printed
SERVICE: status line + last 5 log lines (remove anything secret)
PROBLEMS:
```

---

## Day-to-day (Droplet Console)

| What | Command |
|---|---|
| Is it running? | `systemctl status atlas@default --no-pager` |
| Live log | `journalctl -u atlas@default -f` (Ctrl+C to exit) |
| Health + risk status | `cd /opt/atlas-trader && sudo -u atlas .venv/bin/atlas status` |
| **Emergency stop** (flatten everything + stop) | `sudo -u atlas touch /opt/atlas-trader/state/KILL` |
| Stop / start | `systemctl stop atlas@default` / `systemctl start atlas@default` |
| Update to the newest code | `cd /opt/atlas-trader && sudo -u atlas git pull && systemctl restart atlas@default` |
| After an emergency stop, re-enable | `sudo -u atlas rm /opt/atlas-trader/state/KILL && systemctl start atlas@default` |

## Later: guardian for the $50k practice demo
Once the $50k demo exists and its ID is in `config/topstep50k.yaml`:
`systemctl enable --now atlas@topstep50k`. It runs alongside the first bot.
