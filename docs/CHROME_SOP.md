# Atlas Trader: Claude in Chrome setup SOP

Before you start, log in yourself (Claude should never type your passwords) to:
1. Your prop firm dashboard (Toptraders / Top One Trader)
2. TradeLocker web platform (the DEMO account)
3. TradingView
4. GitHub (github.com/RicardoTyson1/atlas-trader)

Then paste everything in the box below into Claude in Chrome.

---

```
You are helping me set up my automated trading system, "Atlas Trader".
I am already logged in to my prop firm dashboard, TradeLocker (demo account),
TradingView, and GitHub in this browser. Your job is to COLLECT INFORMATION and
DOWNLOAD DATA only.

HARD RULES. Follow them at all times:
- Do NOT place, modify, or close any trade or order. Do not click Buy or Sell.
- Do NOT change any account, risk, or platform settings.
- Do NOT buy, upgrade, or start any challenge, subscription, or payment.
- Do NOT type, reveal, copy, or store any password, API key, or 2FA code.
  If a page asks for one, stop and ask me.
- If anything is unclear or a page looks different from what I describe, stop and ask me.
- Write numbers exactly as shown on the page. If you cannot find something, write
  NOT FOUND. Never guess.

TASK 1: PROP FIRM RULES (prop firm dashboard / rules or FAQ page)
For MY current account/plan, record:
  - Firm name, plan/program name, phase (evaluation phase 1, phase 2, or funded)
  - Account size
  - Profit target (% and $)
  - Daily loss limit (% and $), and HOW it is calculated: from start-of-day balance,
    start-of-day equity, the higher of the two, or the initial balance? Is it based on
    floating (open) equity?
  - Max drawdown / max loss (% and $): static or trailing? If trailing, does it trail
    balance or equity, and does it stop trailing (lock) at some level?
  - Minimum trading days, any maximum time limit
  - News trading rules (allowed? blackout window in minutes?)
  - Weekend/overnight holding rules
  - Consistency rules (e.g. no single day > X% of profit)
  - Max lot size or max position limits
  - Rules about EAs, bots, copy trading, or automated trading. Copy the exact wording.
  - Which platforms/servers are allowed (TradeLocker server name if shown)
  - The URL of the page each rule came from

TASK 2: TRADELOCKER INSTRUMENT SPECS (TradeLocker web, DEMO account)
For each of NAS100, US500, US30 (or the closest names your broker uses; record
the exact symbol name), open the instrument info/specification panel and record:
  - Exact symbol name
  - Contract size (or "value per point / per lot")
  - Minimum lot, lot step, maximum lot
  - Tick size and tick value (if shown)
  - Typical spread shown right now (write the time you checked)
  - Commission (if shown)
  - Trading session hours and timezone
  - Leverage / margin requirement
Also record:
  - The SERVER name shown for this account (as on the login screen)
  - The account number / account ID
  - Whether this is a DEMO or LIVE account (it must be DEMO; if not, stop and tell me)
  - Account currency and current balance

TASK 3: TRADINGVIEW DATA EXPORT
In TradingView:
  1. Open a chart of NAS100. Prefer my broker's feed if available (search "NAS100"
     and pick the broker/CFD version, e.g. GatesFX, or else the closest CFD feed such as
     "PEPPERSTONE:NAS100" or "OANDA:NAS100USD"). Record exactly which symbol you used.
  2. Set the timeframe to 5 minutes.
  3. Scroll the chart back as far as it will load (press Home or drag left
     repeatedly) so the maximum history is loaded.
  4. Chart menu (the arrow next to the layout name, or right-click) → "Export chart data…"
     → Time format: ISO time → Export. Save the CSV.
  5. Repeat for timeframe 1 minute (save a second CSV).
  6. Repeat steps 1–4 for US500 and US30 at 5 minutes, if possible.
  7. Record the file names, symbol, timeframe, first date and last date of each file.
If the export option is not available on my TradingView plan, stop and tell me.

TASK 4: UPLOAD THE CSV FILES TO GITHUB
  1. Go to https://github.com/RicardoTyson1/atlas-trader
  2. Navigate into the "data" folder, then "Add file" → "Upload files".
  3. Upload the CSV files into a folder named data/raw/ (type "raw/" before the file
     name if needed so they land in data/raw/).
  4. Commit message: "Add TradingView price exports". Commit directly to main.
  5. Record the GitHub URL of the uploaded folder.

TASK 5: FINAL REPORT
Produce ONE report in exactly this format so I can paste it to my developer:

=== ATLAS SETUP REPORT ===
Date checked:
PROP FIRM
  firm / plan / phase:
  account size:
  profit target:
  daily loss limit (value + how calculated):
  max drawdown (value + static/trailing + trails on + lock level):
  min trading days / time limit:
  news rule:
  overnight/weekend rule:
  consistency rule:
  max lots:
  bots/EA rule (exact wording):
  source URLs:
TRADELOCKER (DEMO)
  server name:
  account number:
  demo or live:
  currency / balance:
  NAS100 -> symbol | contract size/value per point | min lot | lot step | max lot | spread (time) | commission | session hours
  US500  -> ...
  US30   -> ...
TRADINGVIEW EXPORTS
  file | symbol used | timeframe | first date | last date
GITHUB
  upload URL:
NOT FOUND / QUESTIONS:
=== END ===
```

---

When Claude in Chrome finishes, copy the report and paste it into your Claude
Code session. The developer side (backtests, config, bot testing) continues from there.
