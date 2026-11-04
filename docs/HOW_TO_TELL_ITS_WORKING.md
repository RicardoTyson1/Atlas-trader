# How to tell the bot is working

You should never have to wonder. These signals tell you, from most to least automatic.

## 1. Silence alarm (healthchecks.io): catches the bot or server dying
The bot checks in every 30 seconds. If healthchecks.io hears nothing for 10+ minutes,
**it** emails/Telegrams you ("Atlas 10k bot is DOWN"). This works even if the whole
server is dead, because the alarm lives outside it.
- Normal: no messages from healthchecks.io.
- Action if it fires: run `systemctl status atlas@default` in the Droplet Console.

## 2. Telegram messages from the bot
| Message | Meaning |
|---|---|
| `Atlas started in DEMO mode` | Bot (re)started. Expected after updates/restarts. |
| `Good morning. ready...` | Each trading day: balance, today's day stop and account stop. **No morning message = something is wrong.** |
| `BUY/SELL ... SL ... TP ...` | Bot opened a trade, stop-loss and take-profit attached at the broker. |
| `Closed ...: P/L $...` | A trade closed (stop, target, exit or you). A loss starts the 15-min cooldown. |
| `Manual trade accepted ...` | Your hand-placed trade passed the rules. |
| `GUARDIAN closing ... (reason)` | A trade broke a rule and was closed. |
| `DAILY LOSS LIMIT hit ... Done for today` | Day stop reached; no more trades until tomorrow. |
| `Day report: P/L ...` | Each evening: day P/L, trades, guardian actions, halts. **No day report = something is wrong.** |
| `Bot cannot complete its checks ...` | Broker/API trouble; open trades keep their broker stops; bot retries. |
| `COULD NOT CLOSE ... CHECK THE ACCOUNT NOW` | Rare. Open TradeLocker and close it yourself. |
| `FAILED TO START` | Bad login or broker down; it retries every 5 minutes. |

## 3. On demand: `atlas status`
```
cd /opt/atlas-trader && sudo -u atlas .venv/bin/atlas status
```
Shows **ALIVE** if the bot checked in during the last 3 minutes, plus equity, trades
today, cooldown, halts, open positions and whether the kill switch is on.

## 4. The weekly truth check (with me)
Export the week's trades from TradeLocker and send them over. I compare what the bot
did with what it should have done and with the backtest.

## What "working" does NOT mean
"Working" means the bot follows the rules every time. It does not mean every day is
green. Losing days with a -$49 loss are the system working; a -$37,000 day is what it
exists to prevent.

## Verified on 2026-11-04 (live, demo #2594911)
11/11 live checks passed: bot entry with broker-side stop, no double orders, loss
detection incl. fees (bot -$0.16 = account -$0.16), cooldown blocks bot and manual
re-entry, cooldown survives restart, guardian closes oversize / no-stop / add-on /
cooldown trades and confirms each close, valid manual trade accepted, kill switch
flattens and stops. The 24/7 loop ran with the real config and reported ALIVE.
