"""Read-only: GatesFX TradeLocker overnight financing (swap) settings for XAUUSD.R.
Holding a trade past the daily rollover is charged a swap; research backtests do not include it.
Prints only swap / financing / rollover fields of the instrument details (no account data).
Run on the server from /opt/atlas-trader:
  sudo -u atlas bash -c 'set -a; . ./.env; set +a; .venv/bin/python research/tl_swap_check.py'
"""
from __future__ import annotations

import json
import os

from tradelocker import TLAPI

api = TLAPI(environment="https://demo.tradelocker.com", username=os.environ["TL_EMAIL"],
            password=os.environ["TL_PASSWORD"], server=os.environ["TL_SERVER"])
iid = int(api.get_instrument_id_from_symbol_name("XAUUSD.R"))
d = api.get_instrument_details(iid)
keep = ("swap", "financ", "rollover", "overnight", "interest", "triple", "commission", "lotsize", "contract")


def walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            walk(v, f"{path}.{k}" if path else k)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            walk(v, f"{path}[{i}]")
    elif any(w in path.lower() for w in keep):
        print(f"{path} = {x}")


walk(d)
print("all top-level keys:", json.dumps(sorted(d.keys()) if isinstance(d, dict) else str(type(d))))
