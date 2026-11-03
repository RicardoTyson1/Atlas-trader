"""Strategy base class.

A strategy turns bars into a signal frame aligned to the bars:
    signal : +1 long, -1 short, 0 none   (decided at the CLOSE of that bar)
    stop   : protective stop price
    target : take-profit price, or NaN to use `rr` x risk from the actual fill
    rr     : reward/risk multiple used when target is NaN

Rules every strategy must follow:
  * Only use data up to and including the current bar (tests enforce this).
  * Every signal has a stop on the correct side of price.
"""
from __future__ import annotations

from typing import Any, ClassVar

import numpy as np
import pandas as pd

from ..config import Session
from ..data import add_session_columns


def _mins(t) -> int:
    return t.hour * 60 + t.minute


class Strategy:
    name: ClassVar[str] = "base"
    defaults: ClassVar[dict[str, Any]] = {}

    def __init__(self, session: Session, **params: Any) -> None:
        unknown = set(params) - set(self.defaults)
        if unknown:
            raise ValueError(f"{self.name}: unknown params {sorted(unknown)}")
        self.session = session
        self.p = {**self.defaults, **params}

    # --- helpers -------------------------------------------------------
    def _prep(self, df: pd.DataFrame) -> pd.DataFrame:
        return add_session_columns(df, self.session.timezone)

    def _entry_window(self, d: pd.DataFrame) -> pd.Series:
        s = self.session
        # bar closes at tod + bar_minutes; we decide at the close
        close_tod = d["tod"] + _bar_minutes(d)
        return (
            (close_tod >= _mins(s.trade_start))
            & (close_tod <= _mins(s.last_entry))
            & d["weekday"].isin(s.weekdays)
        )

    @staticmethod
    def _empty(index: pd.Index) -> pd.DataFrame:
        return pd.DataFrame(
            {"signal": 0, "stop": np.nan, "target": np.nan, "rr": np.nan}, index=index
        )

    @staticmethod
    def _finalize(out: pd.DataFrame, close: pd.Series) -> pd.DataFrame:
        """Drop signals whose stop/target are on the wrong side or missing."""
        long_bad = (out["signal"] == 1) & ~(out["stop"] < close)
        short_bad = (out["signal"] == -1) & ~(out["stop"] > close)
        tgt = out["target"]
        long_bad |= (out["signal"] == 1) & tgt.notna() & ~(tgt > close)
        short_bad |= (out["signal"] == -1) & tgt.notna() & ~(tgt < close)
        bad = long_bad | short_bad
        out.loc[bad, ["signal"]] = 0
        out.loc[out["signal"] == 0, ["stop", "target", "rr"]] = np.nan
        out["signal"] = out["signal"].astype(int)
        return out

    def generate(self, df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover
        raise NotImplementedError


def _bar_minutes(d: pd.DataFrame) -> int:
    if len(d) < 2:
        return 1
    diffs = pd.Series(d.index).diff().dropna()
    return max(1, int(diffs.mode().iloc[0].total_seconds() // 60))
