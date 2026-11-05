"""Trade journal + weekly keep/drop review (demo learning mode).

Every closed trade is appended to `state/journal.csv`. Every Friday after the 17:00 New York close
the runner sends a Telegram report built here: each strategy x session (by entry time, NY) with
trades, win rate, net $ and average R, for the week and for everything since the journal began.

Verdict per strategy x session (cumulative, never automatic: Ricardo decides):
  KEEP        >= MIN_TRADES trades and average R > 0
  DROP?       >= MIN_TRADES trades and average R < 0
  collecting  fewer than MIN_TRADES trades (too early to judge)
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta
from pathlib import Path

FIELDS = ["close_time", "strategy", "symbol", "side", "entry_time", "pnl", "risk_usd", "r"]
MIN_TRADES = 30
SESSIONS = [("Asia 18-02", 18 * 60, 2 * 60), ("London 02-08", 2 * 60, 8 * 60),
            ("NY morning 08-11", 8 * 60, 11 * 60), ("NY day 11-17", 11 * 60, 17 * 60)]


def append(path: str | Path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    new = not p.exists()
    with p.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def load(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def session_of(ts: datetime) -> str:
    m = ts.hour * 60 + ts.minute
    for name, a, b in SESSIONS:
        if (a <= m < b) if a < b else (m >= a or m < b):
            return name
    return "other"


def _stats(rows: list[dict]) -> dict:
    pnl = [float(r["pnl"]) for r in rows]
    rs = [float(r["r"]) for r in rows if r.get("r") not in ("", None)]
    return {"n": len(rows), "win": 100.0 * sum(p > 0 for p in pnl) / len(pnl) if pnl else 0.0,
            "net": sum(pnl), "avg_r": sum(rs) / len(rs) if rs else 0.0}


def weekly_report(path: str | Path, now_local: datetime) -> str:
    rows = load(path)
    tz = now_local.tzinfo
    for r in rows:
        r["_entry"] = datetime.fromisoformat(r["entry_time"]).astimezone(tz)
        r["_close"] = datetime.fromisoformat(r["close_time"]).astimezone(tz)
        r["_key"] = (r["strategy"], session_of(r["_entry"]))
    week_start = now_local - timedelta(days=7)
    week = [r for r in rows if r["_close"] >= week_start]
    keys = sorted({r["_key"] for r in rows})
    lines = [f"WEEKLY REVIEW {now_local:%Y-%m-%d}: {len(week)} trades this week, {len(rows)} since the journal began."]
    w = _stats(week) if week else None
    if w:
        lines.append(f"Week: {w['win']:.0f}% wins, net ${w['net']:,.0f}, avg {w['avg_r']:+.2f}R")
    if not keys:
        lines.append("No closed trades recorded yet.")
        return "\n".join(lines)
    lines.append("Strategy | session: this week // since start -> verdict")
    for k in keys:
        allk = [r for r in rows if r["_key"] == k]
        wk = [r for r in week if r["_key"] == k]
        a = _stats(allk)
        verdict = ("collecting" if a["n"] < MIN_TRADES else "KEEP" if a["avg_r"] > 0 else "DROP?")
        ws = _stats(wk) if wk else None
        week_txt = f"{ws['n']} tr ${ws['net']:,.0f}" if ws else "0 tr"
        lines.append(f"- {k[0]} | {k[1]}: {week_txt} // {a['n']} tr, {a['win']:.0f}% win, "
                     f"${a['net']:,.0f}, {a['avg_r']:+.2f}R -> {verdict}")
    lines.append(f"Verdicts need {MIN_TRADES}+ trades. Nothing is switched off automatically.")
    return "\n".join(lines)
