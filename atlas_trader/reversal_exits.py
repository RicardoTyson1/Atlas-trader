"""Exit variants for reversal_trail's target study (research; not wired into the bot).

reversal_trail fades a fast move: the move runs from `start` to its extreme `ext`, and the trade
bets on a retrace back toward `start`. Its live target is 50% of that move.

  fib_target(side, ext, start, frac)   price of a `frac` retrace of the move
                                       (0.5 = live target, 1.0 = back to the start of the move).
  split_qty(lots, lot_step, min_lot)   split a position into the half closed at the target and the
                                       half left to trail, in whole lot steps. A position too small
                                       to split goes out whole at the target.
  SwitchTrail                          "no fixed target": the current candle trail (from +1R) keeps
                                       running, and once a CLOSED candle has reached the 50% level
                                       the trail runs from 0R (any profit) instead of waiting for 1R.

All decisions use closed candles only; a new stop applies from the next candle.
"""
from __future__ import annotations

import math

import numpy as np

from .backtest import trail_candidate


def fib_target(side: int, ext: float, start: float, frac: float) -> float:
    """Price at a `frac` retrace of the move from `start` to `ext`, in the trade's direction."""
    if side not in (1, -1):
        raise ValueError("side must be +1 or -1")
    if frac <= 0:
        raise ValueError("frac must be > 0")
    return float(ext + side * frac * abs(start - ext))


def split_qty(lots: float, lot_step: float, min_lot: float, frac: float = 0.5) -> tuple[float, float]:
    """(lots closed at the target, lots left to trail). The closed part is `frac` of the position
    rounded DOWN to the lot step; both parts must be >= min_lot, otherwise nothing is split and the
    whole position is closed at the target (returns (lots, 0.0))."""
    if lots <= 0 or lot_step <= 0:
        raise ValueError("lots and lot_step must be > 0")
    steps = round(lots / lot_step)
    lots = steps * lot_step                                   # snap to the broker's grid
    first_steps = math.floor(steps * frac + 1e-9)
    first, rest = first_steps * lot_step, (steps - first_steps) * lot_step
    if first + 1e-12 < min_lot or rest + 1e-12 < min_lot:
        return round(lots, 10), 0.0
    return round(first, 10), round(rest, 10)


class SwitchTrail:
    """Stop manager for variant 7 (no fixed target). Feed the last 3 CLOSED candles each bar."""

    def __init__(self, side: int, entry: float, init_stop: float, level: float,
                 trail_start_r: float, trail_buf: float) -> None:
        self.side, self.entry, self.init_stop = side, float(entry), float(init_stop)
        self.level = float(level)
        self.trail_start_r, self.trail_buf = float(trail_start_r), float(trail_buf)
        self.reached = False

    def update(self, o3, h3, l3, c3, cur_stop: float) -> float | None:
        """o3..c3 = the last 3 closed candles, oldest first. Returns a new stop or None."""
        ext = h3[-1] if self.side == 1 else l3[-1]
        if self.side * (ext - self.level) >= 0:
            self.reached = True
        start = 0.0 if self.reached else self.trail_start_r
        return trail_candidate(self.side, self.entry, self.init_stop, cur_stop, start, self.trail_buf,
                               np.asarray(o3, float), np.asarray(h3, float), np.asarray(l3, float),
                               np.asarray(c3, float))
