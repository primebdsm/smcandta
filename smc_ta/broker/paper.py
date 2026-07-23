"""Paper broker for demo and offline testing."""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from smc_ta.broker.models import AccountState, OrderFill, OrderRequest, Position, utc_now
from smc_ta.forex.pairs import infer_pip_size


class PaperBroker:
    """Simple broker simulator for market-order research workflows."""

    def __init__(
        self,
        *,
        initial_balance: float = 10_000.0,
        account_currency: str = "USD",
        default_spread_pips: float = 1.2,
        slippage_pips: float = 0.1,
        commission_per_order: float = 0.0,
    ) -> None:
        if initial_balance <= 0:
            raise ValueError("initial_balance must be positive")
        self.balance = float(initial_balance)
        self.account_currency = account_currency.upper()
        self.default_spread_pips = float(default_spread_pips)
        self.slippage_pips = float(slippage_pips)
        self.commission_per_order = float(commission_per_order)
        self.positions: dict[str, Position] = {}
        self.fills: list[OrderFill] = []
        self.last_prices: dict[str, float] = {}

    def _execution_price(self, symbol: str, side: str, market_price: float) -> tuple[float, float, float]:
        pip_size = infer_pip_size(symbol)
        spread = self.default_spread_pips * pip_size
        slippage = self.slippage_pips * pip_size
        if side == "buy":
            return market_price + spread / 2 + slippage, spread, slippage
        return market_price - spread / 2 - slippage, spread, slippage

    def mark_price(self, symbol: str, market_price: float) -> None:
        """Store the latest mid price for account equity calculations."""

        self.last_prices[symbol.upper()] = float(market_price)

    def get_account(self) -> AccountState:
        equity = self.balance
        for position in self.get_open_positions():
            mark = self.last_prices.get(position.symbol, position.entry_price)
            equity += position.unrealized_pnl(mark)
        return AccountState(
            balance=self.balance,
            equity=equity,
            free_margin=equity,
            currency=self.account_currency,
        )

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        symbol_filter = symbol.upper() if symbol else None
        return [
            position
            for position in self.positions.values()
            if position.is_open and (symbol_filter is None or position.symbol == symbol_filter)
        ]

    def place_order(
        self,
        request: OrderRequest,
        *,
        market_price: float,
        timestamp: datetime | None = None,
    ) -> OrderFill:
        """Open a position with a market order."""

        if request.order_type != "market":
            raise ValueError("PaperBroker currently supports market orders only")
        if request.units <= 0:
            raise ValueError("order units must be positive")

        symbol = request.symbol.upper()
        side = "long" if request.side == "buy" else "short"
        price, spread, slippage = self._execution_price(symbol, request.side, market_price)
        fill_time = timestamp or utc_now()
        order_id = uuid4().hex
        fill = OrderFill(
            order_id=order_id,
            symbol=symbol,
            side=request.side,
            units=float(request.units),
            price=price,
            spread=spread,
            slippage=slippage,
            commission=self.commission_per_order,
            timestamp=fill_time,
            client_order_id=request.client_order_id,
        )
        self.balance -= self.commission_per_order
        self.positions[order_id] = Position(
            position_id=order_id,
            symbol=symbol,
            side=side,
            units=float(request.units),
            entry_price=price,
            opened_at=fill_time,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            metadata=dict(request.metadata),
        )
        self.fills.append(fill)
        self.mark_price(symbol, market_price)
        return fill

    def close_position(
        self,
        position_id: str,
        *,
        market_price: float,
        timestamp: datetime | None = None,
    ) -> OrderFill:
        """Close an open paper position."""

        return self.close_position_units(position_id, units=None, market_price=market_price, timestamp=timestamp)

    def close_position_units(
        self,
        position_id: str,
        *,
        units: float | None,
        market_price: float,
        timestamp: datetime | None = None,
    ) -> OrderFill:
        """Close all or part of an open paper position."""

        if position_id not in self.positions:
            raise KeyError(f"unknown position_id: {position_id}")
        position = self.positions[position_id]
        if not position.is_open:
            raise ValueError(f"position is already closed: {position_id}")
        close_units = position.units if units is None else min(float(units), position.units)
        if close_units <= 0:
            raise ValueError("close units must be positive")

        close_side = "sell" if position.side == "long" else "buy"
        price, spread, slippage = self._execution_price(position.symbol, close_side, market_price)
        close_time = timestamp or utc_now()
        if position.side == "long":
            pnl = (price - position.entry_price) * close_units
        else:
            pnl = (position.entry_price - price) * close_units
        realized = pnl - self.commission_per_order
        position.realized_pnl += realized
        self.balance += realized
        position.units -= close_units
        if position.units <= 1e-9:
            position.units = 0.0
            position.closed_at = close_time
            position.exit_price = price

        fill = OrderFill(
            order_id=uuid4().hex,
            symbol=position.symbol,
            side=close_side,
            units=close_units,
            price=price,
            spread=spread,
            slippage=slippage,
            commission=self.commission_per_order,
            timestamp=close_time,
        )
        self.fills.append(fill)
        self.mark_price(position.symbol, market_price)
        return fill
