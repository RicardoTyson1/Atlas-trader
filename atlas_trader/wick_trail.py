"""Ricardo's confirmed wick trail (P1) with an optional break-even step at +1R (P2).

Rules (long; a short is the exact mirror). Closed candles only: feed each candle to
`WickTrail.update` AFTER it has closed; a stop it returns takes effect from the NEXT candle.

  STRONG-BODY CANDLE : body >= `body_frac` (60%) of the candle's range AND body >= `body_atr`
                       (0.5) x ATR(14) on the trail timeframe, closing in the trade's direction.
  ACTIVATION         : nothing happens until price has reached +`activate_r` x initial risk
                       (a closed candle's high >= entry + 1R). Before that the initial stop
                       stays. Only candles from the activation candle on are used.
  PULLBACK           : after a strong bullish candle S, every following candle until a new
                       confirmation. Pullback low = lowest low of those candles. It counts only
                       if at least one of them has a low below the previous candle's low.
  CONFIRMATION       : a new strong bullish candle that closes above S's high.
  STOP MOVE          : at the confirmation candle's close, new stop = pullback low - buffer
                       (`buffer_atr` x ATR at that candle). Applied only if higher than the
                       current stop (forward only) and below the confirmation close. The
                       confirmation candle becomes the new S.
                       A strong candle that closes above S's high with no valid pullback
                       before it moves nothing and becomes the new S.
  OPTIONS (for the bounded retest; defaults = the rules above):
    `confirm_on="close"` : confirmation only needs to close above S's CLOSE (not its high).
    `require_dip=False`  : any pullback counts, even without a lower low.
  BREAK-EVEN (P2)    : at activation, the stop moves to entry + `be_offset` (the costs of the
                       round trip, so a stop-out nets ~0R), forward only. If that candle closed
                       below the break-even price, it is applied at the first later close above it.
"""
from __future__ import annotations


def strong_body(side: int, o: float, h: float, l: float, c: float, atr: float,
                body_frac: float = 0.6, body_atr: float = 0.5) -> bool:
    """True if the candle is a strong-body candle in the direction `side` (+1 bull, -1 bear)."""
    rng = h - l
    body = side * (c - o)
    return rng > 0 and atr > 0 and body > 0 and body >= body_frac * rng and body >= body_atr * atr


class WickTrail:
    """Stateful stop manager for ONE open trade. Mirror trick: a short is handled as a long on
    negated prices, so both sides run exactly the same code."""

    def __init__(self, side: int, entry: float, init_stop: float, buffer_atr: float,
                 breakeven: bool = False, be_offset: float = 0.0, activate_r: float = 1.0,
                 body_frac: float = 0.6, body_atr: float = 0.5, confirm_on: str = "high",
                 require_dip: bool = True) -> None:
        if confirm_on not in ("high", "close"):
            raise ValueError("confirm_on must be 'high' or 'close'")
        if side not in (1, -1):
            raise ValueError("side must be +1 or -1")
        if side * (entry - init_stop) <= 0:
            raise ValueError("initial stop must be on the losing side of the entry")
        self.side = side
        self.buffer_atr = float(buffer_atr)
        self.activate_r = float(activate_r)
        self.body_frac, self.body_atr = float(body_frac), float(body_atr)
        self.confirm_on, self.require_dip = confirm_on, bool(require_dip)
        # everything below is in "long space" (prices multiplied by side)
        self._entry = side * entry
        self._stop = side * init_stop
        self._risk0 = self._entry - self._stop
        self._be_price = self._entry + float(be_offset)
        self._be_pending = bool(breakeven)
        self.active = False
        self._s_high: float | None = None      # high (or close, if confirm_on="close") of strong candle S
        self._pb_low: float | None = None      # lowest low of the pullback since S
        self._pb_dip = False                   # a pullback candle undercut the previous candle's low
        self._prev_low: float | None = None
        self.moves = 0

    @property
    def stop(self) -> float:
        return self.side * self._stop

    def update(self, o: float, h: float, l: float, c: float, atr: float) -> float | None:
        """Feed one CLOSED candle (and the ATR value at that candle). Returns the new stop if it
        moved, else None. The returned stop applies from the next candle."""
        s = self.side
        o, c = s * o, s * c
        h, l = (h, l) if s == 1 else (-l, -h)          # mirror: a short's high is the long-space low
        moved: float | None = None

        if not self.active:
            if h >= self._entry + self.activate_r * self._risk0:
                self.active = True
            else:
                return None

        if self._be_pending and c > self._be_price:
            self._be_pending = False
            if self._be_price > self._stop:
                self._stop = moved = self._be_price

        strong = strong_body(1, o, h, l, c, atr, self.body_frac, self.body_atr)
        if self._s_high is None:
            if strong:
                self._start_s(h if self.confirm_on == "high" else c)
        elif strong and c > self._s_high:
            if self._pb_low is not None and (self._pb_dip or not self.require_dip):
                new = self._pb_low - self.buffer_atr * atr
                if new > self._stop and new < c:
                    self._stop = moved = new
            self._start_s(h if self.confirm_on == "high" else c)
        else:                                            # a pullback candle
            self._pb_low = l if self._pb_low is None else min(self._pb_low, l)
            if self._prev_low is not None and l < self._prev_low:
                self._pb_dip = True
        self._prev_low = l

        if moved is not None:
            self.moves += 1
            return s * moved
        return None

    def _start_s(self, high: float) -> None:
        self._s_high = high
        self._pb_low = None
        self._pb_dip = False
