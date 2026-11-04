"""TradeLocker broker adapter (works for any TradeLocker broker / prop firm).

Wraps the official SDK: pip install "atlas-trader[tradelocker]".
Credentials come from environment variables (see .env.example), never from code.
"""
from __future__ import annotations

import logging
import os
import time as _time

import pandas as pd

from ..data import normalize, timeframe_to_offset
from .base import Account, Broker, Position

log = logging.getLogger(__name__)

BOT_TAG = "atlas"  # strategyId stamped on every bot order; positions carry it too

# TradeLocker rate limits (GET /trade/config): positions 1/s, account state 4 per 2s,
# quotes 10/s, history 3/s, orders 5/s. Stay under them with a small margin.
_MIN_INTERVAL = {"positions": 1.1, "account": 0.6, "quote": 0.15, "history": 0.4}


class TradeLockerBroker(Broker):
    def __init__(self, environment_url: str, email: str | None = None, password: str | None = None,
                 server: str | None = None, acc_num: int | None = None,
                 account_id: int | None = None, allowed_account_ids: list[int] | None = None):
        try:
            from tradelocker import TLAPI
        except ImportError as e:  # pragma: no cover
            raise ImportError('Install the SDK: pip install "atlas-trader[tradelocker]"') from e
        email = email or os.environ.get("TL_EMAIL")
        password = password or os.environ.get("TL_PASSWORD")
        server = server or os.environ.get("TL_SERVER")
        acc = acc_num or int(os.environ.get("TL_ACC_NUM") or 0)
        acc_id = account_id or int(os.environ.get("TL_ACCOUNT_ID") or 0)
        if not (email and password and server):
            raise RuntimeError("Set TL_EMAIL, TL_PASSWORD and TL_SERVER (see .env.example)")
        self.api = TLAPI(environment=environment_url, username=email, password=password,
                         server=server, account_id=acc_id, acc_num=acc, log_level="info")
        if allowed_account_ids is not None and int(self.api.account_id) not in allowed_account_ids:
            raise RuntimeError(
                f"Connected to account {self.api.account_id}, which is not in the allowed list "
                f"{allowed_account_ids}. Refusing to trade. Set TL_ACCOUNT_ID to your demo account."
            )
        self._ids: dict[str, int] = {}
        self._last_call: dict[str, float] = {}
        log.info("Connected to TradeLocker %s account id %s (accNum %s)",
                 environment_url, self.api.account_id, self.api.acc_num)

    def _throttle(self, route: str) -> None:
        wait = self._last_call.get(route, 0.0) + _MIN_INTERVAL[route] - _time.monotonic()
        if wait > 0:
            _time.sleep(wait)
        self._last_call[route] = _time.monotonic()

    def _iid(self, symbol: str) -> int:
        if symbol not in self._ids:
            self._ids[symbol] = int(self.api.get_instrument_id_from_symbol_name(symbol))
        return self._ids[symbol]

    def bars(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        step = pd.Timedelta(timeframe_to_offset(timeframe))
        now = pd.Timestamp.now(tz="UTC")
        # markets close at weekends/daily breaks, so ask for extra calendar time
        start = now - step * int(count * 1.5) - pd.Timedelta(days=3)
        df = self.history(symbol, timeframe, start, now, pause=0.0)
        if df.empty:
            return df
        df = df[df.index + step <= now]   # completed bars only
        return df.tail(count)

    def history(self, symbol: str, timeframe: str, start: pd.Timestamp, end: pd.Timestamp,
                chunk_rows: int = 25000, pause: float = 1.0) -> pd.DataFrame:
        """Download a long range of bars in chunks (oldest first)."""
        import time as _t

        step = pd.Timedelta(timeframe_to_offset(timeframe))
        chunk = step * chunk_rows
        frames = []
        t0 = start
        while t0 < end:
            t1 = min(t0 + chunk, end)
            self._throttle("history")
            raw = self.api.get_price_history(
                self._iid(symbol), resolution=timeframe,
                start_timestamp=int(t0.timestamp() * 1000), end_timestamp=int(t1.timestamp() * 1000),
            )
            # the SDK keeps every price-history response in memory; drop it so a
            # 24/7 process does not grow by hundreds of MB
            self.api.__dict__.pop("__cached__request_history_cacheable", None)
            if raw is not None and not raw.empty:
                frames.append(raw)
            t0 = t1
            if pause:
                _t.sleep(pause)
        if not frames:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        raw = pd.concat(frames)
        df = raw.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
        df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
        return normalize(df.drop(columns=["t"]))

    def spec(self, symbol: str) -> dict:
        d = self.api.get_instrument_details(self._iid(symbol))
        return {k: d.get(k) for k in ("lotSize", "lotStep", "minLot", "maxLot", "leverage")}

    def account(self) -> Account:
        self._throttle("account")
        st = self.api.get_account_state()
        bal = float(st.get("balance"))
        eq = st.get("projectedBalance", st.get("equity"))
        return Account(balance=bal, equity=float(eq) if eq is not None else bal)

    def positions(self) -> list[Position]:
        self._throttle("positions")
        df = self.api.get_all_positions()
        out: list[Position] = []
        if df is None or df.empty:
            return out
        names = {v: k for k, v in self._ids.items()}
        for _, r in df.iterrows():
            iid = int(r["tradableInstrumentId"])
            sym = names.get(iid) or self.api.get_symbol_name_from_instrument_id(iid)
            sl = r.get("stopLossId")
            has_stop = sl is not None and not pd.isna(sl) and str(sl) not in ("0", "", "None")
            opened = pd.to_datetime(int(r["openDate"]), unit="ms", utc=True) if r.get("openDate") else None
            tag = r.get("strategyId")
            out.append(Position(
                id=str(r["id"]), symbol=sym, side=1 if r["side"] == "buy" else -1,
                qty=float(r["qty"]), entry=float(r["avgPrice"]), has_stop=has_stop, opened=opened,
                tag=None if tag is None or pd.isna(tag) else str(tag),
            ))
        return out

    def quote(self, symbol: str) -> tuple[float, float]:
        self._throttle("quote")
        q = self.api.get_quotes(self._iid(symbol))
        return float(q["bp"]), float(q["ap"])

    def _bot_positions(self, symbol: str) -> list[Position]:
        return [p for p in self.positions() if p.symbol == symbol and p.tag == BOT_TAG]

    def market_bracket(self, symbol: str, side: int, qty: float, stop: float, target: float) -> str:
        """Send ONE market order with broker-side SL/TP. Returns the resulting POSITION id.

        The SDK retries failed requests by default; for an order that could send the
        same order up to 4 times (e.g. after a slow reply), so retries are disabled
        for this call and the outcome is checked against the account instead.
        """
        before = {p.id for p in self._bot_positions(symbol)}
        orig = self.api._request

        def no_retry(*args, **kwargs):
            kwargs["retry_request"] = False
            return orig(*args, **kwargs)

        self.api._request = no_retry  # instance attribute shadows the method for this call
        err: Exception | None = None
        oid = None
        try:
            oid = self.api.create_order(
                self._iid(symbol), quantity=qty, side="buy" if side > 0 else "sell", type_="market",
                stop_loss=stop, stop_loss_type="absolute", take_profit=target, take_profit_type="absolute",
                strategy_id=BOT_TAG,
            )
        except Exception as e:  # the order may still have been accepted
            err = e
        finally:
            del self.api._request
        # find the position the order produced (fills can take a moment to show)
        for _ in range(6):   # positions are throttled to ~1/s, so this waits ~6s
            new = [p for p in self._bot_positions(symbol) if p.id not in before]
            if new:
                return new[0].id
        if err is not None:
            raise RuntimeError(f"Order for {symbol} failed and no position appeared: {err!r}") from err
        if oid is None:
            raise RuntimeError(f"Order rejected for {symbol}")
        raise RuntimeError(f"Order {oid} for {symbol} accepted but no position appeared within ~6s")

    def close_position(self, position_id: str) -> None:
        self.api.close_position(position_id=int(position_id))

    def close_all(self, symbol: str | None = None) -> None:
        # Positions only. Never bulk-delete orders here: on TradeLocker a position's
        # stop-loss/take-profit ARE orders, and deleting them leaves the trade naked
        # (verified on demo) if the close itself does not fill (e.g. market closed).
        self.api.close_all_positions(instrument_id_filter=self._iid(symbol) if symbol else 0)
