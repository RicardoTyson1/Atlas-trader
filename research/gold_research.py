"""Pre-registered gold (XAUUSD.R) strategy research.

Protocol (fixed BEFORE looking at results):
  * Data: GatesFX 5m bars, 2 years. Last 30% of days = HOLDOUT, never used for
    selection. First 70% = DEV, split into two halves (dev1, dev2).
  * Edge is measured in R per trade with fine-grained sizing and no risk halts.
  * A config is a CANDIDATE only if expectancy > 0 in BOTH dev halves, dev t-stat >= 1.5,
    and >= 60 dev trades. Per family, the candidate with the best worst-half
    expectancy is evaluated ONCE on the holdout.
  * A strategy PASSES only if holdout expectancy > 0 with t-stat >= 1.5.

Run:  python research/gold_research.py data/raw/XAUUSD.R_5m.csv
"""
from __future__ import annotations

import copy
import itertools
import json
import sys
from datetime import time
from multiprocessing import Pool

import numpy as np
import pandas as pd

from atlas_trader.backtest import run_backtest, split_in_out
from atlas_trader.config import load_config
from atlas_trader.data import load_csv, resample
from atlas_trader.strategies import build_strategy

SYM = "XAUUSD.R"


def grid(**axes):
    keys = list(axes)
    for combo in itertools.product(*axes.values()):
        yield dict(zip(keys, combo))


FAMILIES = {
    "london_breakout": dict(
        strategy="session_breakout", tf="5m", overnight=False,
        configs=[dict(range_start="00:00", trade_end="08:00", **g) for g in grid(
            range_end=["02:00", "03:00"], exit_time=["11:30", "15:50"],
            reward_risk=[1.5, 2.0, 3.0], stop_mode=["opposite", "mid"])],
    ),
    "comex_open_breakout": dict(
        strategy="session_breakout", tf="5m", overnight=False,
        configs=[dict(range_start="08:20", trade_end="11:00", exit_time="15:50", **g) for g in grid(
            range_end=["08:35", "08:50", "09:20"], reward_risk=[1.5, 2.0, 3.0],
            stop_mode=["opposite", "mid"])],
    ),
    "donchian_1h": dict(
        strategy="donchian_trend", tf="1H", overnight=True,
        configs=[g for g in grid(entry_bars=[20, 55, 100], exit_bars=[10, 20, 50], stop_atr=[2.0, 3.0])
                 if g["exit_bars"] < g["entry_bars"]],
    ),
    "donchian_4h": dict(
        strategy="donchian_trend", tf="4H", overnight=True,
        configs=list(grid(entry_bars=[20, 55], exit_bars=[10, 20], stop_atr=[2.0, 3.0])),
    ),
    "evening_long_drift": dict(
        strategy="time_drift", tf="1H", overnight=True,
        configs=[dict(direction=1, stop_atr=3.0, **g) for g in grid(
            entry_time=["19:00", "20:00"], hold_bars=[3, 6, 9])],
    ),
}


def research_cfg(overnight: bool):
    cfg = load_config("config/default.yaml")
    cfg.prop.daily_loss_pct = cfg.prop.max_drawdown_pct = 1e6
    cfg.prop.profit_target_pct = 1e9
    cfg.risk.max_consecutive_losses = 10**6
    cfg.risk.max_trades_per_day = 50
    cfg.risk.max_lots = 1e12
    cfg.risk.max_room_fraction_per_trade = 1.0
    cfg.session.trade_start, cfg.session.last_entry = time(0, 0), time(23, 59)
    cfg.session.overnight = overnight
    inst = copy.deepcopy(cfg.instrument(SYM))
    inst.min_lot = inst.lot_step = 1e-9
    return cfg, inst


def r_stats(r: pd.Series) -> dict:
    n = len(r)
    if n < 3:
        return {"n": n, "expR": float(r.mean()) if n else 0.0, "t": 0.0, "win%": 0.0}
    return {"n": n, "expR": float(r.mean()), "t": float(r.mean() / (r.std() / np.sqrt(n))),
            "win%": float(100 * (r > 0).mean())}


DATA: dict[str, pd.DataFrame] = {}


def _run(job):
    fam, params, part = job
    spec = FAMILIES[fam]
    cfg, inst = research_cfg(spec["overnight"])
    df = DATA[(spec["tf"], part)]
    t = run_backtest(df, build_strategy(spec["strategy"], cfg.session, params), inst, cfg).trades
    return fam, json.dumps(params, sort_keys=True), part, (t["r"] if len(t) else pd.Series(dtype=float))


def main(path: str) -> None:
    base = load_csv(path)
    dev, hold = split_in_out(base, 0.3)
    dev1, dev2 = split_in_out(dev, 0.5)
    for tf in {"5m", "1H", "4H"}:
        for name, part in (("dev1", dev1), ("dev2", dev2), ("dev", dev), ("hold", hold)):
            DATA[(tf, name)] = part if tf == "5m" else resample(part, tf)
    print(f"DEV {dev.index[0].date()} -> {dev.index[-1].date()} | HOLDOUT {hold.index[0].date()} -> {hold.index[-1].date()}")
    n_cfg = sum(len(f["configs"]) for f in FAMILIES.values())
    print(f"{len(FAMILIES)} families, {n_cfg} pre-registered configs\n")

    jobs = [(fam, p, part) for fam, spec in FAMILIES.items() for p in spec["configs"] for part in ("dev1", "dev2")]
    with Pool() as pool:
        res = pool.map(_run, jobs)
    table: dict[tuple, dict] = {}
    for fam, key, part, r in res:
        table.setdefault((fam, key), {})[part] = r

    rows = []
    for (fam, key), parts in table.items():
        d1, d2 = r_stats(parts["dev1"]), r_stats(parts["dev2"])
        alld = r_stats(pd.concat([parts["dev1"], parts["dev2"]]))
        rows.append({"family": fam, "params": key, "dev_n": alld["n"], "dev_expR": alld["expR"], "dev_t": alld["t"],
                     "dev1_expR": d1["expR"], "dev2_expR": d2["expR"], "worst_half": min(d1["expR"], d2["expR"])})
    allrows = pd.DataFrame(rows)
    pd.set_option("display.width", 250, "display.max_colwidth", 90)
    print("Best 3 per family on DEV (by worst-half expectancy):")
    print(allrows.sort_values("worst_half", ascending=False).groupby("family").head(3)
          .round(3).to_string(index=False))

    cands = allrows[(allrows.dev1_expR > 0) & (allrows.dev2_expR > 0) & (allrows.dev_t >= 1.5) & (allrows.dev_n >= 60)]
    picks = cands.sort_values("worst_half", ascending=False).groupby("family").head(1)
    print(f"\n{len(cands)} of {len(allrows)} configs meet the DEV bar; {len(picks)} families have a candidate.\n")
    if picks.empty:
        print("RESULT: no candidate survived development. Nothing goes to the holdout.")
        return
    hold_jobs = [(r.family, json.loads(r.params), "hold") for r in picks.itertuples()]
    with Pool() as pool:
        hres = pool.map(_run, hold_jobs)
    print("HOLDOUT (evaluated once):")
    for fam, key, _, r in hres:
        s = r_stats(r)
        verdict = "PASS" if s["expR"] > 0 and s["t"] >= 1.5 else "FAIL"
        print(f"  {verdict}  {fam:<22} {key}  n={s['n']} expR={s['expR']:+.3f} t={s['t']:+.2f} win={s['win%']:.1f}%")
    bh = DATA[("1H", "hold")]
    print(f"\nBenchmark: gold buy&hold over holdout {100*(bh.close.iloc[-1]/bh.close.iloc[0]-1):+.1f}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else f"data/raw/{SYM}_5m.csv")
