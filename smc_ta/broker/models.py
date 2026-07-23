"""Broker-neutral execution models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

OrderSide = Literal["buy", "sell"]
OrderType = Literal["market"]
PositionSide = Literal["long", "short"]


def utc_now() -> datetime:
    """Return timezone-aware UTC now."""

    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class OrderRequest:
    """Broker-neutral order request.

    This repository implements market orders for paper/demo workflows. Live
    adapters can extend the same model with broker-native IDs and validation.
    """

    symbol: str
    side: OrderSide
    units: float
    order_type: OrderType = "market"
    stop_loss: float | None = None
    take_profit: float | None = None
    client_order_id: str = field(default_factory=lambda: uuid4().hex)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderFill:
    """Executed order fill."""

    order_id: str
    symbol: str
    side: OrderSide
    units: float
    price: float
    spread: float
    slippage: float
    commission: float
    timestamp: datetime
    client_order_id: str | None = None


@dataclass
class Position:
    """Open or closed Forex position."""

    position_id: str
    symbol: str
    side: PositionSide
    units: float
    entry_price: float
    opened_at: datetime
    stop_loss: float | None = None
    take_profit: float | None = None
    closed_at: datetime | None = None
    exit_price: float | None = None
    realized_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def unrealized_pnl(self, mark_price: float) -> float:
        """Return quote-currency PnL before conversion to account currency."""

        if self.side == "long":
            return (mark_price - self.entry_price) * self.units
        return (self.entry_price - mark_price) * self.units


@dataclass(frozen=True)
class AccountState:
    """Account state snapshot."""

    balance: float
    equity: float
    margin_used: float = 0.0
    free_margin: float = 0.0
    currency: str = "USD"
    timestamp: datetime = field(default_factory=utc_now)

