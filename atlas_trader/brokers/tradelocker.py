"""TradeLocker broker adapter (works for any TradeLocker broker / prop firm).

Wraps the official SDK: pip install "atlas-trader[tradelocker]".
Credentials come from environment variables (see .env.example), never from code.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from ..data import normalize
from .base import Account, Broker, Position

log = logging.getLogger(__name__)

_LOOKBACK = {"1m": "2D", "5m": "5D", "15m": "10D", "30m": "15D", "1H": "30D", "4H": "90D", "1D": "365D"}


class TradeLockerBroker(Broker):
    def __init__(self, environment_url: str, email: str | None = None, password: str | None = None,
                 server: str | None = None, acc_num: int | None = None):
        try:
            from tradelocker import TLAPI
        except ImportError as e:  # pragma: no cover
            raise ImportError('Install the SDK: pip install "atlas-trader[tradelocker]"') from e
        email = email or os.environ.get("TL_EMAIL")
        password = password or os.environ.get("TL_PASSWORD")
        server = server or os.environ.get("TL_SERVER")
        acc = acc_num or int(os.environ.get("TL_ACC_NUM") or 0)
        if not (email and password and server):
            raise RuntimeError("Set TL_EMAIL, TL_PASSWORD and TL_SERVER (see .env.example)")
        self.api = TLAPI(environment=environment_url, username=email, password=password,
                         server=server, acc_num=acc, log_level="info")
        self._ids: dict[str, int] = {}
        log.info("Connected to TradeLocker %s account %s", environment_url, self.api.acc_num)

    def _iid(self, symbol: str) -> int:
        if symbol not in self._ids:
            self._ids[symbol] = int(self.api.get_instrument_id_from_symbol_name(symbol))
        return self._ids[symbol]

    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        raw = self.api.get_price_history(self._iid(symbol), resolution=timeframe,
                                         lookback_period=_LOOKBACK.get(timeframe, "5D"))
        if raw is None or raw.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = raw.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
        df = normalize(df.drop(columns=["t"]))
        step = pd.Timedelta(df.index[1] - df.index[0]) if len(df) > 1 else pd.Timedelta(minutes=1)
        now = pd.Timestamp.now(tz="UTC")
        df = df[df.index + step <= now]   # completed bars only
        return df.tail(count)

    def account(self) -> Account:
        st = self.api.get_account_state()
        bal = float(st.get("balance"))
        eq = st.get("projectedBalance", st.get("equity"))
        return Account(balance=bal, equity=float(eq) if eq is not None else bal)

    def positions(self) -> list[Position]:
        df = self.api.get_all_positions()
        out: list[Position] = []
        if df is None or df.empty:
            return out
        names = {v: k for k, v in self._ids.items()}
        for _, r in df.iterrows():
            iid = int(r["tradableInstrumentId"])
            sym = names.get(iid) or self.api.get_symbol_name_from_instrument_id(iid)
            out.append(Position(
                id=str(r["id"]), symbol=sym, side=1 if r["side"] == "buy" else -1,
                qty=float(r["qty"]), entry=float(r["avgPrice"]),
            ))
        return out

    def market_bracket(self, symbol: str, side: int, qty: float, stop: float, target: float) -> str:
        oid = self.api.create_order(
            self._iid(symbol), quantity=qty, side="buy" if side > 0 else "sell", type_="market",
            stop_loss=stop, stop_loss_type="absolute", take_profit=target, take_profit_type="absolute",
            strategy_id="atlas",
        )
        if oid is None:
            raise RuntimeError(f"Order rejected for {symbol}")
        return str(oid)

    def close_all(self, symbol: str | None = None) -> None:
        self.api.close_all_positions(instrument_id_filter=self._iid(symbol) if symbol else 0)
        self.api.delete_all_orders(instrument_id_filter=self._iid(symbol) if symbol else 0)
