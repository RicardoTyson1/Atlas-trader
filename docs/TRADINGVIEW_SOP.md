# TradingView → Atlas bot (Silver Bullet indicator + webhook)

What you get: the indicator `pine/atlas_silver_bullet.pine` draws the 10-11 AM NY
Silver Bullet setups on a 5-minute gold chart. When a setup's entry price is
reached, it sends the bot a webhook. The bot sizes the trade at 0.5% risk,
checks every rule (hours 02:00-12:30 NY, cooldown, -2% daily stop, 3 losses in
a row, one trade at a time, no adding) and places it on the GatesFX demo with
the stop-loss and target attached. Every result goes to Telegram.

Status: research LEAD, not proven (docs/RESULTS_ICT_2026-11-05.md). Demo only.

## 1. Server (DigitalOcean droplet console, as root)

```bash
cd /opt/atlas-trader
sudo -u atlas git pull
sudo -u atlas .venv/bin/pip install -e ".[tradelocker,dev]"
sudo -u atlas .venv/bin/pytest -q            # must say "passed", no "failed"

# webhook secret (only if WEBHOOK_SECRET in .env is empty)
grep -q '^WEBHOOK_SECRET=.\+' .env || sed -i "s/^WEBHOOK_SECRET=.*/WEBHOOK_SECRET=$(openssl rand -hex 20)/" .env
grep '^WEBHOOK_SECRET=' .env                 # copy the value for TradingView (step 2)

cp deploy/atlas@.service /etc/systemd/system/ && systemctl daemon-reload
ufw status | grep -q inactive || ufw allow 80/tcp
systemctl restart atlas@default
sleep 20 && curl -s http://localhost/tv      # must print: atlas webhook ok
```

Also check DigitalOcean → Networking → Firewalls: if a cloud firewall is
attached to the droplet, add an inbound rule for HTTP (TCP 80).

From any computer, `http://YOUR_DROPLET_IP/tv` in a browser must show
`atlas webhook ok`.

## 2. TradingView

Webhooks need a paid plan (Essential or higher) and **2-factor authentication
turned on** in TradingView (Profile → Settings → Security).

1. Open a **5-minute** gold chart (OANDA:XAUUSD is close to GatesFX prices).
2. Pine Editor → paste all of `pine/atlas_silver_bullet.pine` → Save as
   "Atlas Silver Bullet" → Add to chart.
3. Indicator settings → Inputs → "Alerts to the Atlas bot":
   - Webhook secret: paste the WEBHOOK_SECRET value from step 1
   - Bot symbol: `XAUUSD.R`
   The table (top right) must show `Bot alerts: ON -> XAUUSD.R`.
4. Create alert (clock icon → +):
   - Condition: **Atlas Silver Bullet** → **Any alert() function call**
   - Expiration: open-ended
   - Notifications: tick **Webhook URL** → `http://YOUR_DROPLET_IP/tv`
   - Name: Atlas SB → bot. Create.
   (The message box is not used; the indicator writes its own message.)

Keep the secret private: anyone with it and the URL can send the bot trades
(they still go through every risk rule).

## 3. How to tell it's working

- Telegram on restart: "TradingView webhook listening on port 80".
- When a setup fills on the chart: Telegram "BUY 0.xx XAUUSD.R ... (atlas_sb)" or
  "TradingView BUY XAUUSD.R skipped: <reason>" (e.g. cooldown, daily stop,
  price moved too far past the entry).
- TradingView → Alerts → Log shows each alert that fired.
- On the server: `journalctl -u atlas@default -n 50 | grep -i webhook`.

## 4. Turning it off

- Pause the alert in TradingView (bot keeps running, takes no new setups), or
- `touch /opt/atlas-trader/state/KILL` on the server: flattens and stops the bot.

## Notes

- The indicator only sends alerts on a 5-minute chart (the tested timeframe).
- One setup per trading day (day starts 18:00 NY), same as the research.
- The bot enters at market when the chart touches the setup's limit price; if
  price is already more than 0.25R past it (late alert, feed difference), the
  trade is skipped. Alerts older than 2 minutes and repeated alerts are ignored.
- TradingView's gold feed and GatesFX's differ by a few cents; stops come from
  the TradingView chart.
- The bot's own strategy list is empty since 2026-11-05: all entries come from
  this indicator (or your own alerts in the same format, see README).
