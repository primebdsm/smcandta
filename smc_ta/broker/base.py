"""Broker adapter protocol."""

from __future__ import annotations

from typing import Protocol

from smc_ta.broker.models import AccountState, OrderFill, OrderRequest, Position


class BrokerAdapter(Protocol):
    """Minimal interface a live broker adapter should implement."""

    def get_account(self) -> AccountState:
        """Return an account snapshot."""

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        """Return open positions, optionally filtered by symbol."""

    def place_order(self, request: OrderRequest, *, market_price: float) -> OrderFill:
        """Place an order using the latest tradable market price."""

    def close_position(self, position_id: str, *, market_price: float) -> OrderFill:
        """Close a position using the latest tradable market price."""

