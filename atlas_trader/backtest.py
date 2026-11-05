"""Bar-by-bar backtester that uses the same RiskManager as live trading.

Execution model (deliberately conservative):
  * Signal decided at bar close -> market entry at NEXT bar's open.
  * Half the spread + slippage paid on entry and on exit.
  * If stop and target are both inside one bar, the STOP is assumed hit first.
  * Gaps through the stop fill at the open (worse than the stop).
  * Prop-firm limits are checked against the worst intrabar equity.
  * Everything is flattened at `flat_by`; nothing is held overnight.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, Instrument
from .data import add_session_columns
from .risk import RiskManager
from .strategies import Strategy


def _mins(t) -> int:
    return t.hour * 60 + t.minute


@dataclass
class Trade:
    symbol: str
    side: int
    entry_time: pd.Timestamp
    entry: float
    stop: float
    target: float
    lots: float
    exit_time: pd.Timestamp | None = None
    exit: float | None = None
    pnl: float = 0.0
    r: float = 0.0
    reason: str = ""
    max_bars: float = float("nan")
    bars: int = 0
    init_stop: float = float("nan")
    trail_start_r: float = float("nan")   # start trailing once open profit >= this x initial risk
    trail_buf: float = 0.0                # room left beyond the pullback wick (price units)
    spike_pts: float = float("nan")       # close on a candle with a body this big in our favor
    spike_min_r: float = 1.0
    spike_mode: int = 2                   # 1 = take the profit at the close, 2 = lock: stop to mid-candle
    stop_moves: int = 0
    best: float = float("nan")            # best price reached since entry (for the runaway lock)


@dataclass
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.Series          # equity at each bar close
    daily: pd.DataFrame        # per-day pnl and worst intraday drawdown from start-of-day
    stats: dict
    events: list[str] = field(default_factory=list)


def trail_candidate(side: int, entry: float, init_stop: float, cur_stop: float, start_r: float,
                    buf: float, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray) -> float | None:
    """Ricardo's candle trailing rule, shared by the backtester and the live runner.

    `o/h/l/c` are the last 3 COMPLETED bars (oldest first). Once open profit at the last
    close is >= start_r x initial risk, and a pullback candle (bar -2) is followed by a strong
    candle in the trade's direction (bar -1, body >= 50% of range), the stop moves to just
    beyond the pullback wick (+ buf of room). Returns the new stop, or None if no move.
    The stop only ever moves in the trade's favor and stays on the right side of price.
    """
    if len(c) < 3 or np.isnan(start_r):
        return None
    risk0 = abs(entry - init_stop)
    if risk0 <= 0 or side * (c[-1] - entry) < start_r * risk0:
        return None
    rng = h[-1] - l[-1]
    strong = rng > 0 and side * (c[-1] - o[-1]) >= 0.5 * rng
    counter_body = side * (c[-2] - o[-2]) < 0
    dipped = l[-2] < l[-3] if side == 1 else h[-2] > h[-3]
    if not (strong and (counter_body or dipped)):
        return None
    new = l[-2] - buf if side == 1 else h[-2] + buf
    if side * (new - cur_stop) > 0 and side * (c[-1] - new) > 0:
        return float(new)
    return None


def runaway_lock(side: int, entry: float, init_stop: float, cur_stop: float, start_r: float,
                 keep: float, best: float, last_close: float) -> float | None:
    """When price runs away in our favor, don't give most of it back.

    Once the best open profit since entry reaches `start_r` x initial risk, the stop is kept at
    `keep` x that best profit (e.g. keep 0.6 = never give back more than 40% of the best run).
    Shared by the backtester and the live runner. Returns the new stop or None (never loosens).
    """
    if start_r is None or keep is None or np.isnan(start_r) or np.isnan(best):
        return None
    risk0 = abs(entry - init_stop)
    mfe = side * (best - entry)
    if risk0 <= 0 or mfe < start_r * risk0:
        return None
    new = entry + side * keep * mfe
    if side * (new - cur_stop) > 0 and side * (last_close - new) > 0:
        return float(new)
    return None


def spike_exit(side: int, entry: float, init_stop: float, spike_pts: float, min_r: float,
               o: float, c: float) -> bool:
    """Take the big gain: the last completed candle moved hard in the trade's favor
    (body >= spike_pts) and open profit is >= min_r x initial risk -> close at its close."""
    if not spike_pts or np.isnan(spike_pts) or spike_pts <= 0:
        return False
    risk0 = abs(entry - init_stop)
    return side * (c - o) >= spike_pts and risk0 > 0 and side * (c - entry) >= min_r * risk0


def spike_lock(side: int, entry: float, init_stop: float, cur_stop: float, spike_pts: float,
               min_r: float, o: float, c: float) -> float | None:
    """Stay in the move, but bank most of an aggressive candle: if the last completed candle
    moved hard in our favor (body >= spike_pts) while up >= min_r x risk, pull the stop up to
    the middle of that candle. Never moves the stop backwards. Returns the new stop or None."""
    if not spike_exit(side, entry, init_stop, spike_pts, min_r, o, c):
        return None
    new = (o + c) / 2
    return float(new) if side * (new - cur_stop) > 0 and side * (c - new) > 0 else None


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    inst: Instrument,
    cfg: Config,
    initial_balance: float | None = None,
) -> BacktestResult:
    initial = float(initial_balance or cfg.starting_balance)
    sigs = strategy.generate(df)
    d = add_session_columns(df, cfg.session.timezone)
    rm = RiskManager(cfg.prop, cfg.risk, cfg.session, cfg.news, initial)
    exits = getattr(cfg, "exits", None) or {}
    run_start = exits.get("runaway_start_r")
    run_keep = float(exits.get("runaway_keep", 0.6))

    idx = df.index
    n = len(df)
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    sig = sigs["signal"].to_numpy(int)
    sstop = sigs["stop"].to_numpy(float)
    stgt = sigs["target"].to_numpy(float)
    srr = sigs["rr"].to_numpy(float)
    sexit = sigs["exit"].to_numpy(int) if "exit" in sigs else np.zeros(len(sigs), dtype=int)
    smaxb = sigs["max_bars"].to_numpy(float) if "max_bars" in sigs else np.full(len(sigs), np.nan)
    strail = sigs["trail_start_r"].to_numpy(float) if "trail_start_r" in sigs else np.full(len(sigs), np.nan)
    stbuf = sigs["trail_buf"].to_numpy(float) if "trail_buf" in sigs else np.zeros(len(sigs))
    sspk = sigs["spike_pts"].to_numpy(float) if "spike_pts" in sigs else np.full(len(sigs), np.nan)
    sspr = sigs["spike_min_r"].to_numpy(float) if "spike_min_r" in sigs else np.ones(len(sigs))
    sspm = sigs["spike_mode"].to_numpy(float) if "spike_mode" in sigs else np.full(len(sigs), 2.0)
    overnight = cfg.session.overnight
    days = d["day"].to_numpy()
    tz = cfg.session.tz
    bar_delta = (pd.Series(idx).diff().mode().iloc[0]) if n > 1 else pd.Timedelta(minutes=1)
    open_local = idx.tz_convert(tz)
    close_local = (idx + bar_delta).tz_convert(tz)
    close_mins = (close_local.hour * 60 + close_local.minute).to_numpy()
    flat_m = _mins(cfg.session.flat_by)
    half_cost = inst.spread_points / 2 + inst.slippage_points
    vpp = inst.value_per_point

    balance = initial
    pos: Trade | None = None
    pending: tuple | None = None
    trades: list[Trade] = []
    equity = np.empty(n)
    events: list[str] = []
    cur_day = None
    day_rows: list[dict] = []
    day_start_bal = initial
    day_min_eq = initial

    def close_pos(i: int, price: float, reason: str) -> None:
        nonlocal balance, pos
        assert pos is not None
        fill = price - pos.side * half_cost
        gross = pos.side * (fill - pos.entry) * pos.lots * vpp
        pnl = gross - inst.commission_per_lot * pos.lots  # exit commission
        balance += pnl
        risk_pts = abs(pos.entry - pos.init_stop)
        pos.exit_time, pos.exit, pos.reason = idx[i], fill, reason
        pos.pnl = pnl - inst.commission_per_lot * pos.lots  # include entry commission in trade pnl
        pos.r = pos.side * (fill - pos.entry) / risk_pts if risk_pts else 0.0
        rm.record_close(pos.pnl, close_local[i].to_pydatetime())
        trades.append(pos)
        pos = None

    for i in range(n):
        ts_open_local = open_local[i]
        ts_close_local = close_local[i]
        if days[i] != cur_day:
            if cur_day is not None:
                day_rows.append({"day": cur_day, "start": day_start_bal, "end": balance, "min_equity": day_min_eq})
            cur_day = days[i]
            if not overnight:
                pending = None
            rm.new_day(cur_day, balance)
            day_start_bal = balance
            day_min_eq = balance

        # 1) entry at this bar's open
        if pending is not None and pos is None:
            side, stop, tgt, rr, maxb, trs, tbuf, spk, spr, spm = pending
            ok, _ = rm.check_entry(ts_open_local, open_positions=0)
            fill = o[i] + side * half_cost
            if ok and side * (fill - stop) > 0:
                target = tgt if not np.isnan(tgt) else fill + side * rr * abs(fill - stop)
                if side * (target - fill) > 0:
                    lots = rm.size(fill, stop, inst, balance)
                    if lots > 0:
                        balance -= inst.commission_per_lot * lots
                        pos = Trade(inst.symbol, side, idx[i], fill, stop, target, lots, max_bars=maxb,
                                    init_stop=stop, trail_start_r=trs, trail_buf=tbuf,
                                    spike_pts=spk, spike_min_r=spr, spike_mode=spm)
                        rm.record_entry()
        pending = None

        # 2) exits inside this bar (stop first if ambiguous)
        if pos is not None:
            s, st, tg = pos.side, pos.stop, pos.target
            if s == 1:
                if o[i] <= st:
                    close_pos(i, o[i], "stop (gap)")
                elif l[i] <= st:
                    close_pos(i, st, "stop")
                elif o[i] >= tg:
                    close_pos(i, o[i], "target (gap)")
                elif h[i] >= tg:
                    close_pos(i, tg, "target")
            else:
                if o[i] >= st:
                    close_pos(i, o[i], "stop (gap)")
                elif h[i] >= st:
                    close_pos(i, st, "stop")
                elif o[i] <= tg:
                    close_pos(i, o[i], "target (gap)")
                elif l[i] <= tg:
                    close_pos(i, tg, "target")

        # 3) worst-case equity for prop rules
        if pos is not None:
            worst = l[i] if pos.side == 1 else h[i]
            worst_eq = balance + pos.side * (worst - half_cost * pos.side - pos.entry) * pos.lots * vpp
        else:
            worst_eq = balance
        day_min_eq = min(day_min_eq, worst_eq)
        for ev in rm.update(worst_eq, balance, when=str(ts_close_local)):
            events.append(ev)

        # 4) forced flat (session end, news, risk halt, end of day/data),
        #    strategy exit signal, or time exit -> close at this bar's close
        if pos is not None:
            pos.bars += 1
            end_of_data = i + 1 >= n
            reason = rm.must_flatten(ts_close_local)
            if not overnight:
                if reason is None and close_mins[i] >= flat_m:
                    reason = "end of session"
                if reason is None and (end_of_data or days[i + 1] != days[i]):
                    reason = "end of data/day"
            elif reason is None and end_of_data:
                reason = "end of data"
            if reason is None and sexit[i] != 0 and sexit[i] in (2, pos.side):
                reason = "exit signal"
            if reason is None and not np.isnan(pos.max_bars) and pos.bars >= pos.max_bars:
                reason = "time exit"
            if reason is None and pos.spike_mode == 1 and spike_exit(pos.side, pos.entry, pos.init_stop,
                                                                     pos.spike_pts, pos.spike_min_r, o[i], c[i]):
                reason = "big candle profit"
            if reason:
                close_pos(i, c[i], reason)

        # 4b) candle trailing stop, decided at this bar's close, live from the next bar:
        #     once open profit >= trail_start_r x initial risk, after a pullback candle
        #     followed by a strong candle in the trade's direction, move the stop to just
        #     beyond the pullback candle's wick. The stop only ever moves in the trade's favor.
        if pos is not None and not np.isnan(pos.trail_start_r) and i >= 2:
            new = trail_candidate(pos.side, pos.entry, pos.init_stop, pos.stop, pos.trail_start_r,
                                  pos.trail_buf, o[i - 2:i + 1], h[i - 2:i + 1], l[i - 2:i + 1], c[i - 2:i + 1])
            if new is not None:
                pos.stop = new
                pos.stop_moves += 1
        if pos is not None and not np.isnan(pos.trail_start_r) and run_start is not None:
            ext = h[i] if pos.side == 1 else l[i]
            pos.best = ext if np.isnan(pos.best) else (max(pos.best, ext) if pos.side == 1 else min(pos.best, ext))
            new = runaway_lock(pos.side, pos.entry, pos.init_stop, pos.stop, run_start, run_keep, pos.best, c[i])
            if new is not None:
                pos.stop = new
                pos.stop_moves += 1
        if pos is not None and pos.spike_mode == 2:
            new = spike_lock(pos.side, pos.entry, pos.init_stop, pos.stop, pos.spike_pts, pos.spike_min_r, o[i], c[i])
            if new is not None:
                pos.stop = new
                pos.stop_moves += 1

        equity[i] = balance + (
            pos.side * (c[i] - pos.side * half_cost - pos.entry) * pos.lots * vpp if pos else 0.0
        )

        # 5) new signal at this bar's close
        if pos is None and sig[i] != 0 and i + 1 < n and (overnight or days[i + 1] == days[i]):
            pending = (int(sig[i]), float(sstop[i]), float(stgt[i]), float(srr[i]), float(smaxb[i]),
                       float(strail[i]), float(stbuf[i]) if not np.isnan(stbuf[i]) else 0.0,
                       float(sspk[i]), float(sspr[i]) if not np.isnan(sspr[i]) else 1.0,
                       int(sspm[i]) if not np.isnan(sspm[i]) else 2)

    if cur_day is not None:
        day_rows.append({"day": cur_day, "start": day_start_bal, "end": balance, "min_equity": day_min_eq})

    tdf = pd.DataFrame([t.__dict__ for t in trades])
    eq = pd.Series(equity, index=idx, name="equity")
    daily = pd.DataFrame(day_rows)
    if not daily.empty:
        daily["pnl"] = daily["end"] - daily["start"]
        daily["worst_intraday"] = daily["min_equity"] - daily["start"]
    stats = compute_stats(tdf, eq, daily, initial, rm)
    return BacktestResult(tdf, eq, daily, stats, events)


def compute_stats(trades: pd.DataFrame, eq: pd.Series, daily: pd.DataFrame, initial: float, rm: RiskManager) -> dict:
    st: dict = {"initial_balance": initial, "final_balance": float(eq.iloc[-1]) if len(eq) else initial}
    st["net_profit"] = st["final_balance"] - initial
    st["return_pct"] = 100 * st["net_profit"] / initial
    n = len(trades)
    st["trades"] = n
    if n:
        wins = trades[trades.pnl > 0]
        losses = trades[trades.pnl <= 0]
        st["win_rate_pct"] = 100 * len(wins) / n
        st["avg_win"] = float(wins.pnl.mean()) if len(wins) else 0.0
        st["avg_loss"] = float(losses.pnl.mean()) if len(losses) else 0.0
        gl = -losses.pnl.sum()
        st["profit_factor"] = float(wins.pnl.sum() / gl) if gl > 0 else float("inf")
        st["expectancy_per_trade"] = float(trades.pnl.mean())
        st["expectancy_r"] = float(trades.r.mean())
        streak = longest = 0
        for p in trades.pnl:
            streak = streak + 1 if p <= 0 else 0
            longest = max(longest, streak)
        st["longest_losing_streak"] = longest
    peak = eq.cummax()
    dd = (eq - peak)
    st["max_drawdown"] = float(dd.min()) if len(dd) else 0.0
    st["max_drawdown_pct_of_initial"] = 100 * st["max_drawdown"] / initial
    if not daily.empty:
        st["days"] = len(daily)
        st["green_days_pct"] = 100 * float((daily.pnl > 0).mean())
        st["worst_day"] = float(daily.pnl.min())
        st["best_day"] = float(daily.pnl.max())
        st["worst_intraday_dd"] = float(daily.worst_intraday.min())
        sd = daily.pnl.std()
        st["sharpe_daily_annualized"] = float(daily.pnl.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0
    st["prop_breached"] = rm.s.breached
    st["prop_target_hit_on"] = rm.s.target_hit_on
    st["bot_halt"] = rm.s.hard_halt
    return st


def split_in_out(df: pd.DataFrame, oos_fraction: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by whole days: first part in-sample, last `oos_fraction` out-of-sample."""
    days = pd.Index(df.index.normalize().unique())
    cut = days[int(len(days) * (1 - oos_fraction))]
    return df[df.index < cut], df[df.index >= cut]
