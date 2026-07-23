"""Optional MetaTrader 5 broker adapter.

The `MetaTrader5` package and a running terminal are required at runtime. This
module imports MetaTrader5 lazily so the repository remains installable on
systems where MT5 is not available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd

from smc_ta.broker.models import AccountState, OrderFill, OrderRequest, Position
from smc_ta.validation import normalize_ohlcv


class Mt5UnavailableError(RuntimeError):
    """Raised when the optional MetaTrader5 package is unavailable."""


def _load_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise Mt5UnavailableError("Install the MetaTrader5 package and run the MT5 terminal first") from exc
    return mt5


class MetaTrader5Broker:
    """BrokerAdapter implementation for the local MetaTrader 5 terminal."""

    def __init__(
        self,
        *,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        lot_size: int = 100_000,
        deviation: int = 20,
        magic: int = 234000,
    ) -> None:
        self.mt5 = _load_mt5()
        self.lot_size = lot_size
        self.deviation = deviation
        self.magic = magic
        kwargs: dict[str, Any] = {}
        if path:
            kwargs["path"] = path
        if login is not None:
            kwargs["login"] = login
        if password is not None:
            kwargs["password"] = password
        if server is not None:
            kwargs["server"] = server
        if not self.mt5.initialize(**kwargs):
            raise RuntimeError(f"MetaTrader5 initialize failed: {self.mt5.last_error()}")

    def get_account(self) -> AccountState:
        info = self.mt5.account_info()
        if info is None:
            raise RuntimeError(f"MetaTrader5 account_info failed: {self.mt5.last_error()}")
        return AccountState(
            balance=float(info.balance),
            equity=float(info.equity),
            margin_used=float(info.margin),
            free_margin=float(info.margin_free),
            currency=str(info.currency),
        )

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        raw_positions = self.mt5.positions_get(symbol=symbol) if symbol else self.mt5.positions_get()
        if raw_positions is None:
            raise RuntimeError(f"MetaTrader5 positions_get failed: {self.mt5.last_error()}")
        out: list[Position] = []
        for raw in raw_positions:
            side = "long" if raw.type == self.mt5.POSITION_TYPE_BUY else "short"
            out.append(
                Position(
                    position_id=str(raw.ticket),
                    symbol=str(raw.symbol).upper(),
                    side=side,
                    units=float(raw.volume) * self.lot_size,
                    entry_price=float(raw.price_open),
                    opened_at=datetime.fromtimestamp(raw.time, tz=timezone.utc),
                    stop_loss=float(raw.sl) if raw.sl else None,
                    take_profit=float(raw.tp) if raw.tp else None,
                    realized_pnl=float(raw.profit),
                    metadata={"mt5_ticket": raw.ticket, "volume_lots": raw.volume},
                )
            )
        return out

    def place_order(self, request: OrderRequest, *, market_price: float) -> OrderFill:
        symbol = request.symbol.upper()
        self._ensure_symbol(symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"MetaTrader5 symbol_info_tick failed: {self.mt5.last_error()}")
        is_buy = request.side == "buy"
        price = float(tick.ask if is_buy else tick.bid)
        mt5_request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": round(float(request.units) / self.lot_size, 2),
            "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": request.stop_loss or 0.0,
            "tp": request.take_profit or 0.0,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": request.client_order_id[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_RETURN,
        }
        result = self.mt5.order_send(mt5_request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MetaTrader5 order_send failed: {result} {self.mt5.last_error()}")
        return OrderFill(
            order_id=str(result.order or result.deal),
            symbol=symbol,
            side=request.side,
            units=float(request.units),
            price=float(result.price or price),
            spread=abs(float(tick.ask) - float(tick.bid)),
            slippage=abs(float(result.price or price) - market_price),
            commission=0.0,
            timestamp=datetime.now(timezone.utc),
            client_order_id=request.client_order_id,
        )

    def close_position(self, position_id: str, *, market_price: float) -> OrderFill:
        position = self.mt5.positions_get(ticket=int(position_id))
        if not position:
            raise KeyError(f"unknown MT5 position ticket: {position_id}")
        raw = position[0]
        symbol = str(raw.symbol)
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"MetaTrader5 symbol_info_tick failed: {self.mt5.last_error()}")
        close_buy = raw.type == self.mt5.POSITION_TYPE_SELL
        price = float(tick.ask if close_buy else tick.bid)
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": raw.volume,
            "type": self.mt5.ORDER_TYPE_BUY if close_buy else self.mt5.ORDER_TYPE_SELL,
            "position": raw.ticket,
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": "smc_ta_close",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_RETURN,
        }
        result = self.mt5.order_send(request)
        if result is None or result.retcode != self.mt5.TRADE_RETCODE_DONE:
            raise RuntimeError(f"MetaTrader5 close failed: {result} {self.mt5.last_error()}")
        return OrderFill(
            order_id=str(result.order or result.deal),
            symbol=symbol.upper(),
            side="buy" if close_buy else "sell",
            units=float(raw.volume) * self.lot_size,
            price=float(result.price or price),
            spread=abs(float(tick.ask) - float(tick.bid)),
            slippage=abs(float(result.price or price) - market_price),
            commission=0.0,
            timestamp=datetime.now(timezone.utc),
        )

    def _ensure_symbol(self, symbol: str) -> None:
        info = self.mt5.symbol_info(symbol)
        if info is None:
            raise RuntimeError(f"symbol not found in MT5: {symbol}")
        if not info.visible and not self.mt5.symbol_select(symbol, True):
            raise RuntimeError(f"failed to select MT5 symbol: {symbol}")


class MetaTrader5CandleDataSource:
    """Historical candle downloader for the local MetaTrader 5 terminal."""

    TIMEFRAMES = {
        "M1": "TIMEFRAME_M1",
        "M5": "TIMEFRAME_M5",
        "M15": "TIMEFRAME_M15",
        "M30": "TIMEFRAME_M30",
        "H1": "TIMEFRAME_H1",
        "H4": "TIMEFRAME_H4",
        "D1": "TIMEFRAME_D1",
        "D": "TIMEFRAME_D1",
    }

    def __init__(self, broker: MetaTrader5Broker) -> None:
        self.broker = broker
        self.mt5 = broker.mt5

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        mt5_timeframe_name = self.TIMEFRAMES.get(timeframe.upper())
        if not mt5_timeframe_name:
            raise ValueError(f"unsupported MT5 timeframe: {timeframe}")
        mt5_timeframe = getattr(self.mt5, mt5_timeframe_name)
        if start is not None and end is not None:
            rates = self.mt5.copy_rates_range(symbol, mt5_timeframe, pd.Timestamp(start).to_pydatetime(), pd.Timestamp(end).to_pydatetime())
        else:
            count = limit or 500
            rates = self.mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
        if rates is None:
            raise RuntimeError(f"MetaTrader5 rates download failed: {self.mt5.last_error()}")
        frame = pd.DataFrame(rates)
        if frame.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "tick_volume", "spread"])
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame = frame.set_index("time")
        return normalize_ohlcv(frame)

