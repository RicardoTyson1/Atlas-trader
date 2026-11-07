"""Command line: `atlas <command> ...`  (or `python -m atlas_trader ...`)."""
from __future__ import annotations

import argparse
import itertools
import json
import logging
import os
import sys
import threading
from pathlib import Path

import pandas as pd

from .backtest import run_backtest, split_in_out
from .config import Config, load_config
from .data import load_csv, resample, synthetic_intraday
from .montecarlo import simulate_challenge
from .strategies import REGISTRY, build_strategy


def _load_env(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _parse_params(items: list[str] | None) -> dict:
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def _get_data(args, cfg: Config) -> pd.DataFrame:
    if args.synthetic:
        print("!! Using SYNTHETIC random-walk data. Results say nothing about real profitability.\n")
        base = synthetic_intraday(days=args.synthetic, tz=cfg.session.timezone, seed=args.seed)
    else:
        if not args.data:
            sys.exit("Provide --data path/to/bars.csv (export from TradingView) or --synthetic N")
        base = load_csv(args.data, tz=args.data_tz)
    return resample(base, args.tf)


def _fmt(stats: dict) -> str:
    lines = []
    for k, v in stats.items():
        if isinstance(v, float):
            v = f"{v:,.2f}"
        lines.append(f"  {k:<30} {v}")
    return "\n".join(lines)


def _strategy_and_inst(args, cfg):
    spec = next((s for s in cfg.strategies if s.name == args.strategy), None)
    params = {**(spec.params if spec else {}), **_parse_params(args.param)}
    return build_strategy(args.strategy, cfg.session, params), cfg.instrument(args.symbol), params


def cmd_backtest(args, cfg: Config) -> None:
    df = _get_data(args, cfg)
    strat, inst, params = _strategy_and_inst(args, cfg)
    print(f"Strategy {strat.name} {params} on {inst.symbol} {args.tf}: {df.index[0]} -> {df.index[-1]}, {len(df)} bars")
    parts = [("FULL", df)]
    if args.oos:
        ins, oos = split_in_out(df, args.oos)
        parts = [("IN-SAMPLE", ins), ("OUT-OF-SAMPLE", oos)]
    out_dir = Path(cfg.paths.get("report_dir", "reports"))
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, part in parts:
        res = run_backtest(part, strat, inst, cfg)
        print(f"\n== {label} ==\n{_fmt(res.stats)}")
        for ev in res.events[:10]:
            print(f"  event: {ev}")
        tag = f"{strat.name}_{inst.symbol}_{label.lower()}"
        res.trades.to_csv(out_dir / f"{tag}_trades.csv", index=False)
        res.daily.to_csv(out_dir / f"{tag}_daily.csv", index=False)
        res.equity.to_csv(out_dir / f"{tag}_equity.csv")
        if args.mc and not res.daily.empty:
            mc = simulate_challenge(res.daily, cfg.prop, cfg.starting_balance, max_days=args.mc_days, paths=args.mc)
            print(f"\n  Monte Carlo prop challenge ({mc.paths} paths, max {mc.max_days} days):")
            print(f"    pass {mc.p_pass:.1%} | fail {mc.p_fail:.1%} | undecided {mc.p_undecided:.1%}"
                  f" | median days to pass {mc.median_days_to_pass} | fails by {mc.fail_reasons}")
    print(f"\nReports written to {out_dir}/")


def cmd_compare(args, cfg: Config) -> None:
    df = _get_data(args, cfg)
    inst = cfg.instrument(args.symbol)
    rows = []
    for name in REGISTRY:
        res = run_backtest(df, build_strategy(name, cfg.session), inst, cfg)
        s = res.stats
        rows.append({k: s.get(k) for k in ("trades", "win_rate_pct", "profit_factor", "expectancy_r",
                                           "return_pct", "max_drawdown_pct_of_initial", "prop_breached")} | {"strategy": name})
    print(pd.DataFrame(rows).set_index("strategy").round(2).to_string())


def cmd_sweep(args, cfg: Config) -> None:
    """Parameter sweep scored on OUT-OF-SAMPLE data to limit curve fitting."""
    df = _get_data(args, cfg)
    inst = cfg.instrument(args.symbol)
    grid = {}
    for part in args.grid.split(";"):
        k, vals = part.split("=")
        grid[k.strip()] = [json.loads(v) for v in vals.split(",")]
    ins, oos = split_in_out(df, args.oos or 0.3)
    rows = []
    for combo in itertools.product(*grid.values()):
        params = dict(zip(grid.keys(), combo))
        strat = build_strategy(args.strategy, cfg.session, params)
        a = run_backtest(ins, strat, inst, cfg).stats
        b = run_backtest(oos, strat, inst, cfg).stats
        rows.append({**params, "is_trades": a["trades"], "is_pf": a.get("profit_factor"), "is_ret%": a["return_pct"],
                     "oos_trades": b["trades"], "oos_pf": b.get("profit_factor"), "oos_ret%": b["return_pct"],
                     "oos_maxdd%": b["max_drawdown_pct_of_initial"]})
    out = pd.DataFrame(rows).sort_values("oos_ret%", ascending=False)
    print(out.round(2).to_string(index=False))
    print("\nPick parameters that are good in BOTH halves and whose neighbours are also good. "
          "A lone spike is luck, not edge.")


def cmd_simulate(args, cfg: Config) -> None:
    """Replay data through the LIVE runner with a simulated broker (end-to-end dry run)."""
    from .brokers.sim import SimBroker
    from .notify import Notifier
    from .runner import Runner

    if args.synthetic:
        base = synthetic_intraday(days=args.synthetic, tz=cfg.session.timezone, seed=args.seed)
    else:
        base = load_csv(args.data, tz=args.data_tz)
    syms = sorted({s for spec in cfg.strategies for s in spec.instruments})
    broker = SimBroker({s: base for s in syms}, cfg.starting_balance,
                       {s: cfg.instrument(s).value_per_point for s in syms})
    state_dir = Path(cfg.paths.get("state_dir", "state")) / "simulate"
    for f in state_dir.glob("*.json"):
        f.unlink()
    runner = Runner(cfg, broker, Notifier(), state_dir=state_dir)
    step = pd.Timedelta(args.step)
    t = base.index[0].floor(step) + step
    end = base.index[-1] + step
    while t <= end and not runner.stopped:
        broker.set_time(t)
        runner.step(t)
        t += step
    acct = broker.account()
    closes = [f for f in broker.fills if f["action"] == "close"]
    print(f"Simulated {len(closes)} round trips. Final balance {acct.balance:,.2f} "
          f"(start {cfg.starting_balance:,.2f}). Risk state: {runner.rm.summary()}")


def cmd_run(args, cfg: Config) -> None:
    from .brokers.tradelocker import TradeLockerBroker
    from .notify import Notifier
    from .runner import Runner
    from .webhook import serve

    _load_env()
    mode = args.mode
    if mode == "live":
        if cfg.mode != "live" or not args.i_accept_real_money_risk:
            sys.exit("Live trading needs `mode: live` in the config AND --i-accept-real-money-risk.")
        url = cfg.tradelocker.get("live_url", "https://live.tradelocker.com")
        allowed = cfg.tradelocker.get("live_account_ids") or []
        if not allowed:
            sys.exit("Live mode needs `tradelocker.live_account_ids` in the config.")
    else:
        url = cfg.tradelocker.get("demo_url", "https://demo.tradelocker.com")
        allowed = cfg.tradelocker.get("demo_account_ids") or []
        if not allowed:
            sys.exit("Demo mode needs `tradelocker.demo_account_ids` in the config, so the bot can "
                     "never attach to a real-money account by mistake.")
    cfg.mode = mode  # label alerts/logs with the mode actually running
    allowed_ids = [int(a) for a in allowed]
    mon = cfg.monitoring
    notifier = Notifier(label=mon.get("label", "Atlas"), healthcheck_env=mon.get("healthcheck_env", "HEALTHCHECK_URL"))
    try:
        # each config pins its own account, so several bots can share one .env login
        broker = TradeLockerBroker(url, account_id=allowed_ids[0], allowed_account_ids=allowed_ids)
        runner = Runner(cfg, broker, notifier)
    except Exception as e:
        notifier.send(f"FAILED TO START: {e!r}. Will retry in 5 minutes.", logging.CRITICAL)
        notifier.heartbeat(False, repr(e))
        raise
    if args.webhook or cfg.webhook.get("enabled", False):
        threading.Thread(
            target=serve, args=(runner, cfg.webhook.get("host", "0.0.0.0"), int(cfg.webhook.get("port", 80))),
            daemon=True,
        ).start()
    runner.run_forever()  # returns only after the kill switch


def cmd_download(args, cfg: Config) -> None:
    """Download history from TradeLocker into data/raw/<SYMBOL>_<tf>.csv (read-only)."""
    from .brokers.tradelocker import TradeLockerBroker

    _load_env()
    allowed = cfg.tradelocker.get("demo_account_ids") or None
    broker = TradeLockerBroker(cfg.tradelocker.get("demo_url", "https://demo.tradelocker.com"),
                               account_id=int(allowed[0]) if allowed else None,
                               allowed_account_ids=[int(a) for a in allowed] if allowed else None)
    out_dir = Path("data/raw")
    out_dir.mkdir(parents=True, exist_ok=True)
    end = pd.Timestamp.now(tz="UTC").floor("min")
    start = end - pd.Timedelta(days=args.days)
    for sym in args.symbols:
        df = broker.history(sym, args.tf, start, end)
        path = out_dir / f"{sym}_{args.tf}.csv"
        df.rename_axis("time").to_csv(path)
        print(f"{sym}: {len(df)} bars {df.index.min()} -> {df.index.max()} -> {path}  spec {broker.spec(sym)}")


def cmd_spread(args, cfg: Config) -> None:
    """Sample the live bid/ask spread (read-only) and compare it with the spread guard."""
    import time as _time

    from .brokers.tradelocker import TradeLockerBroker

    _load_env()
    allowed = cfg.tradelocker.get("demo_account_ids") or None
    broker = TradeLockerBroker(cfg.tradelocker.get("demo_url", "https://demo.tradelocker.com"),
                               account_id=int(allowed[0]) if allowed else None,
                               allowed_account_ids=[int(a) for a in allowed] if allowed else None)
    inst = cfg.instrument(args.symbol)
    mult = float(cfg.risk.max_spread_mult or 0)
    limit = mult * inst.spread_points if mult > 0 else float("inf")
    spreads = []
    for _ in range(args.samples):
        bid, ask = broker.quote(args.symbol)
        spreads.append(ask - bid)
        print(f"bid {bid:.2f}  ask {ask:.2f}  spread {ask - bid:.2f}"
              + ("  << guard would SKIP" if ask - bid > limit else ""))
        _time.sleep(args.every)
    print(f"normal {inst.spread_points:.2f} | guard limit {limit:.2f} | "
          f"median {sorted(spreads)[len(spreads) // 2]:.2f} | max {max(spreads):.2f}")


def cmd_status(args, cfg: Config) -> None:
    """Plain-English health check from the bot's saved state."""
    import time as _t

    p = Path(cfg.paths.get("state_dir", "state")) / "runner_state.json"
    if not p.exists():
        print("No state yet: the bot has not run with this config.")
        return
    raw = json.loads(p.read_text())
    r, x = raw["risk"], raw.get("extra", {})
    age = _t.time() - p.stat().st_mtime
    alive = age < 180
    print(f"Bot: {cfg.monitoring.get('label', 'Atlas')}  ({args.config})")
    print(f"  Last check-in : {age:,.0f} s ago -> {'ALIVE' if alive else 'NOT CHECKING IN (is the service running?)'}")
    print(f"  Equity        : ${r.get('last_equity', 0):,.2f}   (start of day ${r.get('sod_balance', 0):,.2f})")
    print(f"  Trades today  : {r.get('trades_today', 0)} / {cfg.risk.max_trades_per_day}   losses in a row: {r.get('consec_losses', 0)}")
    print(f"  Cooldown until: {r.get('cooldown_until') or '-'}")
    print(f"  Halt          : {r.get('hard_halt') or r.get('day_halt') or 'none'}")
    print(f"  Limit breached: {r.get('breached') or 'no'}")
    tracked = x.get("tracked", {})
    print(f"  Open positions: {len(tracked)}" + "".join(
        f"\n    - {t['symbol']} ({'bot' if t['bot'] else 'manual'}) id {pid}" for pid, t in tracked.items()))
    kill = Path(cfg.paths.get("kill_switch_file", "state/KILL"))
    print(f"  Kill switch   : {'ON (bot will flatten and stop)' if kill.exists() else 'off'}")


def cmd_reset_halt(args, cfg: Config) -> None:
    from .risk import RiskManager, RiskState

    p = Path(cfg.paths.get("state_dir", "state")) / "runner_state.json"
    raw = json.loads(p.read_text())
    rm = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, cfg.starting_balance, RiskState.from_dict(raw["risk"]))
    rm.reset_hard_halt()
    raw["risk"] = rm.s.to_dict()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(raw, indent=2))
    tmp.replace(p)
    print("Hard halt cleared. Restart the bot service so it loads the change "
          "(a running bot would overwrite this file).")


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="atlas", description="Atlas Trader")
    ap.add_argument("--config", default="config/default.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def data_args(p):
        p.add_argument("--data", help="CSV of OHLCV bars (TradingView export works)")
        p.add_argument("--data-tz", default="UTC", help="timezone of CSV timestamps if they have none")
        p.add_argument("--synthetic", type=int, metavar="DAYS", help="use random-walk test data instead")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--tf", default="5m")
        p.add_argument("--symbol", default="NAS100")

    b = sub.add_parser("backtest", help="backtest one strategy")
    data_args(b)
    b.add_argument("--strategy", default="momentum_rsi", choices=sorted(REGISTRY))
    b.add_argument("--param", action="append", help="override, e.g. --param reward_risk=1.5")
    b.add_argument("--oos", type=float, help="fraction of days held out as out-of-sample, e.g. 0.3")
    b.add_argument("--mc", type=int, nargs="?", const=5000, help="Monte Carlo prop challenge paths")
    b.add_argument("--mc-days", type=int, default=60)
    b.set_defaults(fn=cmd_backtest)

    c = sub.add_parser("compare", help="backtest every strategy with defaults")
    data_args(c)
    c.set_defaults(fn=cmd_compare)

    s = sub.add_parser("sweep", help="parameter sweep scored out-of-sample")
    data_args(s)
    s.add_argument("--strategy", default="momentum_rsi", choices=sorted(REGISTRY))
    s.add_argument("--grid", required=True, help='e.g. "reward_risk=1.5,2,3;range_minutes=5,15,30"')
    s.add_argument("--oos", type=float, default=0.3)
    s.set_defaults(fn=cmd_sweep)

    sm = sub.add_parser("simulate", help="dry-run the live runner on historical data")
    data_args(sm)
    sm.add_argument("--step", default="5min")
    sm.set_defaults(fn=cmd_simulate)

    r = sub.add_parser("run", help="run the bot against TradeLocker (demo by default)")
    r.add_argument("--mode", choices=["demo", "live"], default="demo")
    r.add_argument("--webhook", action="store_true", help="also accept TradingView alerts (or set webhook.enabled: true in the config)")
    r.add_argument("--i-accept-real-money-risk", action="store_true")
    r.set_defaults(fn=cmd_run)

    dl = sub.add_parser("download", help="download price history from TradeLocker (read-only)")
    dl.add_argument("symbols", nargs="+")
    dl.add_argument("--tf", default="5m")
    dl.add_argument("--days", type=int, default=730)
    dl.set_defaults(fn=cmd_download)

    sp = sub.add_parser("spread", help="sample the live spread vs the spread guard (read-only)")
    sp.add_argument("symbol", nargs="?", default="XAUUSD.R")
    sp.add_argument("--samples", type=int, default=10)
    sp.add_argument("--every", type=float, default=2.0)
    sp.set_defaults(fn=cmd_spread)

    st = sub.add_parser("status", help="show saved risk state")
    st.set_defaults(fn=cmd_status)
    rh = sub.add_parser("reset-halt", help="clear a bot hard halt after review")
    rh.set_defaults(fn=cmd_reset_halt)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
