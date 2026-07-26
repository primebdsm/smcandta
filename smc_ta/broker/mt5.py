"""Optional MetaTrader 5 broker adapter.

The `MetaTrader5` package and a running terminal are required at runtime. This
module imports MetaTrader5 lazily so the repository remains installable on
systems where MT5 is not available.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

import pandas as pd

from smc_ta.broker.models import AccountState, BrokerOrder, OrderFill, OrderRequest, OrderSide, Position
from smc_ta.validation import normalize_ohlcv


class Mt5UnavailableError(RuntimeError):
    """Raised when the optional MetaTrader5 package is unavailable."""


class Mt5InitializationError(RuntimeError):
    """Raised when the MetaTrader 5 terminal cannot be initialized."""


class Mt5TerminalValidationError(RuntimeError):
    """Raised when terminal/account state is unsafe for trading."""


class Mt5SymbolValidationError(ValueError):
    """Raised when a symbol or order volume violates MT5 broker metadata."""


class Mt5PriceValidationError(RuntimeError):
    """Raised when current MT5 tick pricing is unsafe for execution."""


class Mt5OrderRejected(RuntimeError):
    """Raised when MT5 rejects an order check, send, or close request."""


def _load_mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]
    except ImportError as exc:
        raise Mt5UnavailableError("Install the MetaTrader5 package and run the MT5 terminal first") from exc
    return mt5


@dataclass(frozen=True)
class Mt5Config:
    """MetaTrader 5 connection and execution-safety settings."""

    path: str | None = None
    login: int | None = None
    password: str | None = None
    server: str | None = None
    lot_size: int = 100_000
    deviation: int = 20
    magic: int = 234000
    symbol_aliases: Mapping[str, str] = field(default_factory=dict)
    max_tick_age_seconds: float = 15.0
    max_spread_points: float | None = None
    check_order_before_send: bool = True
    enforce_terminal_connected: bool = True
    enforce_trade_allowed: bool = True
    allow_real_account: bool = False
    order_filling: str = "RETURN"
    order_time: str = "GTC"
    comment_prefix: str = "smc_ta"

    def connection_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.path:
            kwargs["path"] = self.path
        if self.login is not None:
            kwargs["login"] = self.login
        if self.password is not None:
            kwargs["password"] = self.password
        if self.server is not None:
            kwargs["server"] = self.server
        return kwargs


@dataclass(frozen=True)
class Mt5SymbolSpec:
    """Tradable symbol metadata from the connected MT5 terminal."""

    symbol: str
    broker_symbol: str
    visible: bool
    digits: int
    point: float
    spread_points: float | None = None
    trade_mode: int | None = None
    volume_min: float = 0.0
    volume_max: float | None = None
    volume_step: float = 0.01
    trade_contract_size: float | None = None
    trade_stops_level_points: int = 0
    trade_freeze_level_points: int = 0
    currency_profit: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_terminal(cls, symbol: str, broker_symbol: str, raw: Any) -> "Mt5SymbolSpec":
        return cls(
            symbol=symbol.upper(),
            broker_symbol=broker_symbol,
            visible=bool(getattr(raw, "visible", False)),
            digits=int(getattr(raw, "digits", 5) or 5),
            point=float(getattr(raw, "point", 0.0) or 0.0),
            spread_points=_optional_float(getattr(raw, "spread", None)),
            trade_mode=_optional_int(getattr(raw, "trade_mode", None)),
            volume_min=float(getattr(raw, "volume_min", 0.0) or 0.0),
            volume_max=_optional_float(getattr(raw, "volume_max", None)),
            volume_step=float(getattr(raw, "volume_step", 0.01) or 0.01),
            trade_contract_size=_optional_float(getattr(raw, "trade_contract_size", None)),
            trade_stops_level_points=int(getattr(raw, "trade_stops_level", 0) or 0),
            trade_freeze_level_points=int(getattr(raw, "trade_freeze_level", 0) or 0),
            currency_profit=_optional_str(getattr(raw, "currency_profit", None)),
            metadata=_object_dict(raw),
        )

    def units_to_lots(self, units: float, lot_size: int) -> float:
        lots = abs(float(units)) / float(lot_size)
        self.validate_lots(lots)
        return round(lots, _step_decimals(self.volume_step))

    def validate_lots(self, lots: float) -> None:
        if lots <= 0:
            raise Mt5SymbolValidationError(f"{self.broker_symbol} volume must be positive")
        if self.volume_min and lots + 1e-12 < self.volume_min:
            raise Mt5SymbolValidationError(
                f"{self.broker_symbol} volume {lots:g} below volume_min {self.volume_min:g}"
            )
        if self.volume_max is not None and lots - 1e-12 > self.volume_max:
            raise Mt5SymbolValidationError(
                f"{self.broker_symbol} volume {lots:g} above volume_max {self.volume_max:g}"
            )
        if self.volume_step > 0:
            scaled = lots / self.volume_step
            if abs(scaled - round(scaled)) > 1e-8:
                raise Mt5SymbolValidationError(
                    f"{self.broker_symbol} volume {lots:g} does not match volume_step {self.volume_step:g}"
                )

    def validate_stops(self, side: OrderSide, *, price: float, stop_loss: float | None, take_profit: float | None) -> None:
        min_distance = max(0.0, self.trade_stops_level_points * self.point)
        if stop_loss is not None:
            if side == "buy" and stop_loss >= price:
                raise Mt5SymbolValidationError(f"{self.broker_symbol} buy stop_loss must be below entry price")
            if side == "sell" and stop_loss <= price:
                raise Mt5SymbolValidationError(f"{self.broker_symbol} sell stop_loss must be above entry price")
            if min_distance and abs(price - stop_loss) + 1e-12 < min_distance:
                raise Mt5SymbolValidationError(
                    f"{self.broker_symbol} stop_loss is inside trade_stops_level {self.trade_stops_level_points} points"
                )
        if take_profit is not None:
            if side == "buy" and take_profit <= price:
                raise Mt5SymbolValidationError(f"{self.broker_symbol} buy take_profit must be above entry price")
            if side == "sell" and take_profit >= price:
                raise Mt5SymbolValidationError(f"{self.broker_symbol} sell take_profit must be below entry price")
            if min_distance and abs(take_profit - price) + 1e-12 < min_distance:
                raise Mt5SymbolValidationError(
                    f"{self.broker_symbol} take_profit is inside trade_stops_level {self.trade_stops_level_points} points"
                )


@dataclass(frozen=True)
class Mt5TickSnapshot:
    """Current bid/ask tick from the connected MT5 terminal."""

    symbol: str
    broker_symbol: str
    time: datetime
    bid: float
    ask: float
    last: float | None
    spread: float
    spread_points: float
    flags: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_terminal(cls, symbol: str, spec: Mt5SymbolSpec, raw: Any) -> "Mt5TickSnapshot":
        bid = float(getattr(raw, "bid", 0.0) or 0.0)
        ask = float(getattr(raw, "ask", 0.0) or 0.0)
        if bid <= 0 or ask <= 0:
            raise Mt5PriceValidationError(f"{spec.broker_symbol} tick is missing valid bid/ask prices")
        spread = ask - bid
        return cls(
            symbol=symbol.upper(),
            broker_symbol=spec.broker_symbol,
            time=_mt5_time(raw),
            bid=bid,
            ask=ask,
            last=_optional_float(getattr(raw, "last", None)),
            spread=spread,
            spread_points=spread / spec.point if spec.point else 0.0,
            flags=_optional_int(getattr(raw, "flags", None)),
            metadata=_object_dict(raw),
        )

    def execution_price(self, side: OrderSide) -> float:
        return self.ask if side == "buy" else self.bid

    def age_seconds(self, now: datetime | None = None) -> float:
        current = datetime.now(timezone.utc) if now is None else _utc_datetime(now)
        return max(0.0, (current - self.time).total_seconds())


@dataclass(frozen=True)
class Mt5ReadinessCheck:
    """One non-trading MT5 readiness check."""

    component: str
    code: str
    severity: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class Mt5ReadinessReport:
    """Non-trading MT5 terminal/account/symbol readiness report."""

    checks: tuple[Mt5ReadinessCheck, ...]
    account: AccountState | None = None
    symbols: tuple[Mt5SymbolSpec, ...] = ()
    ticks: tuple[Mt5TickSnapshot, ...] = ()

    @property
    def ok(self) -> bool:
        return not any(check.blocking for check in self.checks)

    def summary(self) -> str:
        if self.ok:
            warnings = [f"warning:{check.code}" for check in self.checks if check.severity == "warning"]
            return ";".join(warnings) if warnings else "mt5_terminal_ready"
        return ";".join(check.code for check in self.checks if check.blocking)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([asdict(check) for check in self.checks])


class MetaTrader5Broker:
    """BrokerAdapter implementation for the local MetaTrader 5 terminal."""

    def __init__(
        self,
        *,
        config: Mt5Config | None = None,
        path: str | None = None,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        lot_size: int = 100_000,
        deviation: int = 20,
        magic: int = 234000,
        symbol_aliases: Mapping[str, str] | None = None,
        max_tick_age_seconds: float = 15.0,
        max_spread_points: float | None = None,
        check_order_before_send: bool = True,
        enforce_terminal_connected: bool = True,
        enforce_trade_allowed: bool = True,
        allow_real_account: bool = False,
        mt5_module: Any | None = None,
    ) -> None:
        self.config = config or Mt5Config(
            path=path,
            login=login,
            password=password,
            server=server,
            lot_size=lot_size,
            deviation=deviation,
            magic=magic,
            symbol_aliases=symbol_aliases or {},
            max_tick_age_seconds=max_tick_age_seconds,
            max_spread_points=max_spread_points,
            check_order_before_send=check_order_before_send,
            enforce_terminal_connected=enforce_terminal_connected,
            enforce_trade_allowed=enforce_trade_allowed,
            allow_real_account=allow_real_account,
        )
        self.mt5 = mt5_module or _load_mt5()
        if not self.mt5.initialize(**self.config.connection_kwargs()):
            raise Mt5InitializationError(f"MetaTrader5 initialize failed: {self.mt5.last_error()}")

    def get_account(self) -> AccountState:
        info = self.mt5.account_info()
        if info is None:
            raise Mt5TerminalValidationError(f"MetaTrader5 account_info failed: {self.mt5.last_error()}")
        return AccountState(
            balance=float(info.balance),
            equity=float(info.equity),
            margin_used=float(info.margin),
            free_margin=float(info.margin_free),
            currency=str(info.currency),
        )

    def terminal_info(self) -> dict[str, Any]:
        """Return a JSON-friendly MT5 terminal_info snapshot."""

        info = self.mt5.terminal_info()
        if info is None:
            raise Mt5TerminalValidationError(f"MetaTrader5 terminal_info failed: {self.mt5.last_error()}")
        return _object_dict(info)

    def get_symbol_spec(self, symbol: str) -> Mt5SymbolSpec:
        """Return MT5 broker metadata for a requested Forex symbol."""

        clean = symbol.upper()
        broker_symbol = self._broker_symbol(clean)
        info = self._ensure_symbol(clean)
        return Mt5SymbolSpec.from_terminal(clean, broker_symbol, info)

    def get_tick(self, symbol: str) -> Mt5TickSnapshot:
        """Return and validate the latest bid/ask tick for a symbol."""

        clean = symbol.upper()
        spec = self.get_symbol_spec(clean)
        raw = self.mt5.symbol_info_tick(spec.broker_symbol)
        if raw is None:
            raise Mt5PriceValidationError(f"MetaTrader5 symbol_info_tick failed: {self.mt5.last_error()}")
        tick = Mt5TickSnapshot.from_terminal(clean, spec, raw)
        self.validate_tick_snapshot(tick)
        return tick

    def validate_tick_snapshot(self, snapshot: Mt5TickSnapshot, *, now: datetime | None = None) -> None:
        """Raise when current MT5 pricing is stale, inverted, or too wide."""

        if snapshot.ask < snapshot.bid:
            raise Mt5PriceValidationError(f"{snapshot.broker_symbol} tick has ask below bid")
        if self.config.max_tick_age_seconds >= 0:
            age = snapshot.age_seconds(now)
            if age > self.config.max_tick_age_seconds:
                raise Mt5PriceValidationError(
                    f"{snapshot.broker_symbol} tick is stale: {age:.2f}s > {self.config.max_tick_age_seconds:.2f}s"
                )
        if self.config.max_spread_points is not None and snapshot.spread_points > self.config.max_spread_points:
            raise Mt5PriceValidationError(
                f"{snapshot.broker_symbol} spread {snapshot.spread_points:.2f} points above limit {self.config.max_spread_points:.2f}"
            )

    def practice_readiness(self, symbols: list[str] | tuple[str, ...]) -> Mt5ReadinessReport:
        """Run non-trading MT5 terminal, account, symbol, and tick checks."""

        checks: list[Mt5ReadinessCheck] = []
        specs: list[Mt5SymbolSpec] = []
        ticks: list[Mt5TickSnapshot] = []
        account: AccountState | None = None
        checks.extend(self._terminal_checks())
        try:
            self._validate_account_mode_for_trading()
            account = self.get_account()
            checks.append(
                Mt5ReadinessCheck(
                    "account",
                    "account_probe_ok",
                    "info",
                    "MT5 account probe succeeded",
                    {"currency": account.currency, "equity": account.equity, "free_margin": account.free_margin},
                )
            )
        except Exception as exc:
            checks.append(
                Mt5ReadinessCheck(
                    "account",
                    "account_probe_failed",
                    "blocking",
                    str(exc),
                    {"exception_type": type(exc).__name__},
                )
            )
        for symbol in symbols:
            clean = symbol.upper()
            try:
                spec = self.get_symbol_spec(clean)
                self._validate_symbol_trade_mode(spec)
                specs.append(spec)
                checks.append(
                    Mt5ReadinessCheck(
                        "symbol",
                        "symbol_ready",
                        "info",
                        f"{clean} MT5 symbol metadata is usable",
                        {
                            "broker_symbol": spec.broker_symbol,
                            "point": spec.point,
                            "volume_min": spec.volume_min,
                            "volume_step": spec.volume_step,
                            "trade_stops_level_points": spec.trade_stops_level_points,
                        },
                    )
                )
                tick = self.get_tick(clean)
                ticks.append(tick)
                checks.append(
                    Mt5ReadinessCheck(
                        "pricing",
                        "tick_ready",
                        "info",
                        f"{clean} MT5 tick is fresh and usable",
                        {"bid": tick.bid, "ask": tick.ask, "spread_points": tick.spread_points},
                    )
                )
            except Exception as exc:
                checks.append(
                    Mt5ReadinessCheck(
                        "symbol",
                        "symbol_probe_failed",
                        "blocking",
                        str(exc),
                        {"symbol": clean, "exception_type": type(exc).__name__},
                    )
                )
        if checks and not any(check.blocking for check in checks):
            checks.append(
                Mt5ReadinessCheck(
                    "readiness",
                    "mt5_terminal_ready",
                    "info",
                    "MT5 terminal, account, symbols, and ticks passed readiness probes",
                )
            )
        return Mt5ReadinessReport(checks=tuple(checks), account=account, symbols=tuple(specs), ticks=tuple(ticks))

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        broker_symbol = self._broker_symbol(symbol.upper()) if symbol else None
        raw_positions = self.mt5.positions_get(symbol=broker_symbol) if broker_symbol else self.mt5.positions_get()
        if raw_positions is None:
            raise Mt5TerminalValidationError(f"MetaTrader5 positions_get failed: {self.mt5.last_error()}")
        out: list[Position] = []
        for raw in raw_positions:
            side = "long" if raw.type == self.mt5.POSITION_TYPE_BUY else "short"
            raw_symbol = str(raw.symbol)
            out.append(
                Position(
                    position_id=str(raw.ticket),
                    symbol=self._display_symbol(raw_symbol),
                    side=side,
                    units=float(raw.volume) * self.config.lot_size,
                    entry_price=float(raw.price_open),
                    opened_at=datetime.fromtimestamp(raw.time, tz=timezone.utc),
                    stop_loss=float(raw.sl) if raw.sl else None,
                    take_profit=float(raw.tp) if raw.tp else None,
                    realized_pnl=float(raw.profit),
                    metadata={"mt5_ticket": raw.ticket, "mt5_symbol": raw_symbol, "volume_lots": raw.volume},
                )
            )
        return out

    def get_pending_orders(self, symbol: str | None = None) -> list[BrokerOrder]:
        """Return pending MT5 orders as broker-neutral snapshots."""

        broker_symbol = self._broker_symbol(symbol.upper()) if symbol else None
        raw_orders = self.mt5.orders_get(symbol=broker_symbol) if broker_symbol else self.mt5.orders_get()
        if raw_orders is None:
            raise Mt5TerminalValidationError(f"MetaTrader5 orders_get failed: {self.mt5.last_error()}")
        return [
            _broker_order_from_mt5(self.mt5, raw, self._display_symbol(str(raw.symbol)), self.config.lot_size)
            for raw in raw_orders
        ]

    def place_order(self, request: OrderRequest, *, market_price: float) -> OrderFill:
        self._validate_account_mode_for_trading()
        symbol = request.symbol.upper()
        spec = self.get_symbol_spec(symbol)
        self._validate_symbol_trade_mode(spec)
        tick = self.get_tick(symbol)
        is_buy = request.side == "buy"
        price = tick.execution_price(request.side)
        volume = spec.units_to_lots(request.units, self.config.lot_size)
        spec.validate_stops(request.side, price=price, stop_loss=request.stop_loss, take_profit=request.take_profit)
        mt5_request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": spec.broker_symbol,
            "volume": volume,
            "type": self.mt5.ORDER_TYPE_BUY if is_buy else self.mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": request.stop_loss or 0.0,
            "tp": request.take_profit or 0.0,
            "deviation": self.config.deviation,
            "magic": self.config.magic,
            "comment": self._order_comment(request.client_order_id),
            "type_time": self._order_time_constant(),
            "type_filling": self._order_filling_constant(),
        }
        check_result = self._order_check(mt5_request)
        result = self.mt5.order_send(mt5_request)
        self._raise_for_trade_result(result, "MetaTrader5 order_send failed")
        fill_price = float(getattr(result, "price", 0.0) or price)
        return OrderFill(
            order_id=str(getattr(result, "order", None) or getattr(result, "deal", None)),
            symbol=symbol,
            side=request.side,
            units=float(request.units),
            price=fill_price,
            spread=tick.spread,
            slippage=abs(fill_price - market_price),
            commission=0.0,
            timestamp=datetime.now(timezone.utc),
            client_order_id=request.client_order_id,
            metadata={
                "mt5_symbol": spec.broker_symbol,
                "mt5_order": _optional_str(getattr(result, "order", None)),
                "mt5_deal": _optional_str(getattr(result, "deal", None)),
                "mt5_retcode": _optional_int(getattr(result, "retcode", None)),
                "mt5_volume_lots": volume,
                "mt5_order_check_retcode": _optional_int(getattr(check_result, "retcode", None))
                if check_result is not None
                else None,
            },
        )

    def close_position(self, position_id: str, *, market_price: float) -> OrderFill:
        self._validate_account_mode_for_trading()
        position = self.mt5.positions_get(ticket=int(position_id))
        if not position:
            raise KeyError(f"unknown MT5 position ticket: {position_id}")
        raw = position[0]
        broker_symbol = str(raw.symbol)
        display_symbol = self._display_symbol(broker_symbol)
        spec = self.get_symbol_spec(display_symbol)
        tick = self.get_tick(display_symbol)
        close_buy = raw.type == self.mt5.POSITION_TYPE_SELL
        side: OrderSide = "buy" if close_buy else "sell"
        price = tick.execution_price(side)
        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": float(raw.volume),
            "type": self.mt5.ORDER_TYPE_BUY if close_buy else self.mt5.ORDER_TYPE_SELL,
            "position": raw.ticket,
            "price": price,
            "deviation": self.config.deviation,
            "magic": self.config.magic,
            "comment": self._order_comment("close"),
            "type_time": self._order_time_constant(),
            "type_filling": self._order_filling_constant(),
        }
        check_result = self._order_check(request)
        result = self.mt5.order_send(request)
        self._raise_for_trade_result(result, "MetaTrader5 close failed")
        fill_price = float(getattr(result, "price", 0.0) or price)
        return OrderFill(
            order_id=str(getattr(result, "order", None) or getattr(result, "deal", None)),
            symbol=display_symbol,
            side=side,
            units=float(raw.volume) * self.config.lot_size,
            price=fill_price,
            spread=tick.spread,
            slippage=abs(fill_price - market_price),
            commission=0.0,
            timestamp=datetime.now(timezone.utc),
            metadata={
                "mt5_symbol": broker_symbol,
                "mt5_position": str(raw.ticket),
                "mt5_order": _optional_str(getattr(result, "order", None)),
                "mt5_deal": _optional_str(getattr(result, "deal", None)),
                "mt5_retcode": _optional_int(getattr(result, "retcode", None)),
                "mt5_volume_lots": float(raw.volume),
                "mt5_order_check_retcode": _optional_int(getattr(check_result, "retcode", None))
                if check_result is not None
                else None,
            },
        )

    def _terminal_checks(self) -> list[Mt5ReadinessCheck]:
        try:
            info = self.terminal_info()
        except Exception as exc:
            return [
                Mt5ReadinessCheck(
                    "terminal",
                    "terminal_probe_failed",
                    "blocking",
                    str(exc),
                    {"exception_type": type(exc).__name__},
                )
            ]
        checks: list[Mt5ReadinessCheck] = []
        connected = _optional_bool(info.get("connected"))
        trade_allowed = _optional_bool(info.get("trade_allowed"))
        if self.config.enforce_terminal_connected and connected is False:
            checks.append(Mt5ReadinessCheck("terminal", "terminal_not_connected", "blocking", "MT5 terminal is not connected", info))
        else:
            checks.append(Mt5ReadinessCheck("terminal", "terminal_connected", "info", "MT5 terminal connection probe succeeded", info))
        if self.config.enforce_trade_allowed and trade_allowed is False:
            checks.append(Mt5ReadinessCheck("terminal", "terminal_trade_not_allowed", "blocking", "MT5 terminal trading is disabled", info))
        elif trade_allowed is not None:
            checks.append(Mt5ReadinessCheck("terminal", "terminal_trade_allowed", "info", "MT5 terminal trading flag is enabled", info))
        return checks

    def _validate_account_mode_for_trading(self) -> None:
        info = self.mt5.account_info()
        if info is None:
            raise Mt5TerminalValidationError(f"MetaTrader5 account_info failed: {self.mt5.last_error()}")
        trade_mode = _optional_int(getattr(info, "trade_mode", None))
        real_mode = _optional_int(getattr(self.mt5, "ACCOUNT_TRADE_MODE_REAL", None))
        if real_mode is not None and trade_mode == real_mode and not self.config.allow_real_account:
            raise Mt5TerminalValidationError("MT5 real account is blocked unless allow_real_account=True")
        terminal = self.mt5.terminal_info() if hasattr(self.mt5, "terminal_info") else None
        if terminal is not None:
            if self.config.enforce_terminal_connected and _optional_bool(getattr(terminal, "connected", None)) is False:
                raise Mt5TerminalValidationError("MT5 terminal is not connected")
            if self.config.enforce_trade_allowed and _optional_bool(getattr(terminal, "trade_allowed", None)) is False:
                raise Mt5TerminalValidationError("MT5 terminal trading is disabled")

    def _validate_symbol_trade_mode(self, spec: Mt5SymbolSpec) -> None:
        full_mode = _optional_int(getattr(self.mt5, "SYMBOL_TRADE_MODE_FULL", None))
        if full_mode is not None and spec.trade_mode is not None and spec.trade_mode != full_mode:
            raise Mt5SymbolValidationError(f"{spec.broker_symbol} trade_mode is not full trading")

    def _ensure_symbol(self, symbol: str) -> Any:
        broker_symbol = self._broker_symbol(symbol)
        info = self.mt5.symbol_info(broker_symbol)
        if info is None:
            raise Mt5SymbolValidationError(f"symbol not found in MT5: {broker_symbol}")
        if getattr(info, "visible", False):
            return info
        if not self.mt5.symbol_select(broker_symbol, True):
            raise Mt5SymbolValidationError(f"failed to select MT5 symbol: {broker_symbol}")
        selected = self.mt5.symbol_info(broker_symbol)
        if selected is None or not getattr(selected, "visible", False):
            raise Mt5SymbolValidationError(f"MT5 symbol is not visible after selection: {broker_symbol}")
        return selected

    def _order_check(self, request: dict[str, Any]) -> Any | None:
        if not self.config.check_order_before_send or not hasattr(self.mt5, "order_check"):
            return None
        result = self.mt5.order_check(request)
        if result is None:
            raise Mt5OrderRejected(f"MetaTrader5 order_check failed: {self.mt5.last_error()}")
        retcode = _optional_int(getattr(result, "retcode", None))
        if retcode not in self._successful_order_check_retcodes():
            raise Mt5OrderRejected(f"MetaTrader5 order_check rejected request: {result}")
        return result

    def _raise_for_trade_result(self, result: Any, message: str) -> None:
        if result is None:
            raise Mt5OrderRejected(f"{message}: {self.mt5.last_error()}")
        retcode = _optional_int(getattr(result, "retcode", None))
        if retcode not in self._successful_trade_retcodes():
            raise Mt5OrderRejected(f"{message}: {result} {self.mt5.last_error()}")

    def _successful_trade_retcodes(self) -> set[int]:
        return {
            code
            for code in (
                _optional_int(getattr(self.mt5, "TRADE_RETCODE_DONE", None)),
                _optional_int(getattr(self.mt5, "TRADE_RETCODE_PLACED", None)),
                _optional_int(getattr(self.mt5, "TRADE_RETCODE_DONE_PARTIAL", None)),
            )
            if code is not None
        }

    def _successful_order_check_retcodes(self) -> set[int]:
        return {0, *self._successful_trade_retcodes()}

    def _order_filling_constant(self) -> int:
        return _mt5_constant(self.mt5, "ORDER_FILLING", self.config.order_filling)

    def _order_time_constant(self) -> int:
        return _mt5_constant(self.mt5, "ORDER_TIME", self.config.order_time)

    def _order_comment(self, suffix: str) -> str:
        prefix = self.config.comment_prefix[:16]
        text = f"{prefix}:{suffix}" if suffix else prefix
        return text[:31]

    def _broker_symbol(self, symbol: str) -> str:
        clean = symbol.upper()
        return str(self.config.symbol_aliases.get(clean, clean))

    def _display_symbol(self, broker_symbol: str) -> str:
        for display, mapped in self.config.symbol_aliases.items():
            if str(mapped).upper() == broker_symbol.upper():
                return display.upper()
        return broker_symbol.upper()


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
        clean = symbol.upper()
        broker_symbol = self.broker._broker_symbol(clean)
        self.broker._ensure_symbol(clean)
        mt5_timeframe = getattr(self.mt5, mt5_timeframe_name)
        if start is not None and end is not None:
            rates = self.mt5.copy_rates_range(
                broker_symbol,
                mt5_timeframe,
                _utc_datetime(start),
                _utc_datetime(end),
            )
        else:
            count = limit or 500
            rates = self.mt5.copy_rates_from_pos(broker_symbol, mt5_timeframe, 0, count)
        if rates is None:
            raise RuntimeError(f"MetaTrader5 rates download failed: {self.mt5.last_error()}")
        frame = pd.DataFrame(rates)
        if frame.empty:
            return pd.DataFrame(columns=["open", "high", "low", "close", "tick_volume", "spread"])
        frame["time"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        frame = frame.set_index("time")
        return normalize_ohlcv(frame)


def _broker_order_from_mt5(mt5: Any, raw: Any, symbol: str, lot_size: int) -> BrokerOrder:
    order_type = _mt5_order_type_name(mt5, getattr(raw, "type", None))
    lots = _optional_float(getattr(raw, "volume_current", None) or getattr(raw, "volume_initial", None))
    return BrokerOrder(
        order_id=str(getattr(raw, "ticket", "")),
        symbol=symbol,
        order_type=order_type,
        state="PENDING",
        side=_order_side_from_mt5(mt5, getattr(raw, "type", None)),
        units=lots * lot_size if lots is not None else None,
        price=_optional_float(getattr(raw, "price_open", None)),
        stop_loss=_optional_float(getattr(raw, "sl", None)),
        take_profit=_optional_float(getattr(raw, "tp", None)),
        trade_id=_optional_str(getattr(raw, "position_id", None) or getattr(raw, "position_by_id", None)),
        created_at=datetime.fromtimestamp(getattr(raw, "time_setup", 0), tz=timezone.utc)
        if getattr(raw, "time_setup", 0)
        else None,
        client_order_id=_optional_str(getattr(raw, "comment", None)),
        metadata={"mt5_order_type": getattr(raw, "type", None), "mt5_raw": _object_dict(raw)},
    )


def _order_side_from_mt5(mt5: Any, value: Any) -> OrderSide | None:
    buy_types = {
        getattr(mt5, "ORDER_TYPE_BUY", None),
        getattr(mt5, "ORDER_TYPE_BUY_LIMIT", None),
        getattr(mt5, "ORDER_TYPE_BUY_STOP", None),
        getattr(mt5, "ORDER_TYPE_BUY_STOP_LIMIT", None),
    }
    sell_types = {
        getattr(mt5, "ORDER_TYPE_SELL", None),
        getattr(mt5, "ORDER_TYPE_SELL_LIMIT", None),
        getattr(mt5, "ORDER_TYPE_SELL_STOP", None),
        getattr(mt5, "ORDER_TYPE_SELL_STOP_LIMIT", None),
    }
    if value in buy_types:
        return "buy"
    if value in sell_types:
        return "sell"
    return None


def _mt5_order_type_name(mt5: Any, value: Any) -> str:
    for name in (
        "ORDER_TYPE_BUY",
        "ORDER_TYPE_SELL",
        "ORDER_TYPE_BUY_LIMIT",
        "ORDER_TYPE_SELL_LIMIT",
        "ORDER_TYPE_BUY_STOP",
        "ORDER_TYPE_SELL_STOP",
        "ORDER_TYPE_BUY_STOP_LIMIT",
        "ORDER_TYPE_SELL_STOP_LIMIT",
    ):
        if getattr(mt5, name, None) == value:
            return name.replace("ORDER_TYPE_", "")
    return str(value)


def _mt5_constant(mt5: Any, prefix: str, value: str) -> int:
    name = f"{prefix}_{value.upper()}"
    if not hasattr(mt5, name):
        raise Mt5SymbolValidationError(f"MetaTrader5 constant not available: {name}")
    return int(getattr(mt5, name))


def _mt5_time(raw: Any) -> datetime:
    if getattr(raw, "time_msc", None):
        return datetime.fromtimestamp(float(raw.time_msc) / 1000.0, tz=timezone.utc)
    if getattr(raw, "time", None):
        return datetime.fromtimestamp(float(raw.time), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _utc_datetime(value: Any) -> datetime:
    timestamp = pd.Timestamp(value)
    timestamp = timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")
    return timestamp.to_pydatetime()


def _object_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "_asdict"):
        return dict(value._asdict())
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {}


def _step_decimals(step: float) -> int:
    text = f"{step:.10f}".rstrip("0").rstrip(".")
    return len(text.split(".", 1)[1]) if "." in text else 0


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number != 0 else None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)
