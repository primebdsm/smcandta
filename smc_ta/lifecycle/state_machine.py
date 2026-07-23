"""Trade lifecycle state machine."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pandas as pd

from smc_ta.broker.models import OrderFill, OrderRequest
from smc_ta.lifecycle.models import (
    LifecycleEventType,
    LifecycleState,
    TradeLifecycleEvent,
    TradeLifecycleRecord,
    lifecycle_id,
    utc_timestamp,
)
from smc_ta.risk.manager import RiskDecision


ALLOWED_TRANSITIONS: dict[LifecycleState, frozenset[LifecycleState]] = {
    "created": frozenset({"signal", "blocked", "failed", "cancelled"}),
    "signal": frozenset({"approved", "blocked", "failed", "cancelled"}),
    "approved": frozenset({"submitted", "blocked", "failed", "cancelled"}),
    "submitted": frozenset({"open", "failed", "cancelled"}),
    "open": frozenset({"partially_closed", "closed", "failed"}),
    "partially_closed": frozenset({"partially_closed", "closed", "failed"}),
    "blocked": frozenset(),
    "closed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


class TradeLifecycleError(ValueError):
    """Raised for invalid lifecycle transitions."""


class TradeLifecycleStateMachine:
    """Create and transition deterministic trade lifecycle records."""

    def create_from_signal(
        self,
        *,
        symbol: str,
        timestamp: pd.Timestamp,
        signal: pd.Series,
        setup_name: str = "none",
        trade_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Create a lifecycle record from one signal snapshot."""

        now = utc_timestamp(timestamp)
        side = str(signal.get("side", "flat"))
        reasons = _split_reasons(signal.get("reasons", ""))
        record = TradeLifecycleRecord(
            trade_id=trade_id or lifecycle_id(),
            symbol=symbol.upper(),
            side=side,
            state="created",
            created_at=now,
            updated_at=now,
            signal_timestamp=now,
            setup_name=setup_name,
            confidence=_optional_float(signal.get("confidence")),
            entry_price=_optional_float(signal.get("entry_reference")),
            stop_loss=_optional_float(signal.get("stop_reference")),
            take_profit=_optional_float(signal.get("target_reference")),
            reasons=reasons,
            metadata=dict(metadata or {}),
        )
        return self._transition(
            record,
            "signal",
            "signal",
            timestamp=now,
            reason="signal_generated",
            metadata={
                "long_score": _optional_float(signal.get("long_score")),
                "short_score": _optional_float(signal.get("short_score")),
                "reference_rr": _optional_float(signal.get("reference_rr")),
            },
        )

    def approve(
        self,
        record: TradeLifecycleRecord,
        decision: RiskDecision | None = None,
        *,
        order: OrderRequest | None = None,
        timestamp: pd.Timestamp | None = None,
        reason: str = "approved",
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Mark a signal as approved and attach the planned order."""

        resolved_order = order or (decision.order if decision is not None else None)
        updates: dict[str, Any] = {}
        if resolved_order is not None:
            updates.update(_order_updates(resolved_order))
            updates["metadata"] = {**record.metadata, **resolved_order.metadata}
        if decision is not None:
            updates["units"] = decision.units
        return self._transition(
            record,
            "approved",
            "approved",
            timestamp=timestamp,
            reason=reason,
            updates=updates,
            metadata=metadata,
        )

    def block(
        self,
        record: TradeLifecycleRecord,
        reason: str,
        *,
        source: str = "risk",
        timestamp: pd.Timestamp | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Mark a trade attempt as blocked before execution."""

        reasons = tuple(dict.fromkeys((*record.reasons, reason)))
        return self._transition(
            record,
            "blocked",
            "blocked",
            timestamp=timestamp,
            reason=reason,
            updates={"reasons": reasons},
            metadata={"source": source, **dict(metadata or {})},
        )

    def submit(
        self,
        record: TradeLifecycleRecord,
        order: OrderRequest,
        *,
        timestamp: pd.Timestamp | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Mark an approved order as submitted to a broker adapter."""

        return self._transition(
            record,
            "submitted",
            "submitted",
            timestamp=timestamp,
            reason="order_submitted",
            updates={**_order_updates(order), "metadata": {**record.metadata, **order.metadata}},
            metadata=metadata,
        )

    def record_fill(
        self,
        record: TradeLifecycleRecord,
        fill: OrderFill,
        *,
        position_id: str | None = None,
        timestamp: pd.Timestamp | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Mark a submitted market order as open after a fill."""

        filled_units = record.filled_units + float(fill.units)
        previous_value = (record.average_entry_price or 0.0) * record.filled_units
        average_entry = (previous_value + fill.price * fill.units) / filled_units if filled_units else fill.price
        return self._transition(
            record,
            "open",
            "filled",
            timestamp=timestamp or pd.Timestamp(fill.timestamp),
            reason="order_filled",
            updates={
                "broker_order_id": fill.order_id,
                "client_order_id": fill.client_order_id or record.client_order_id,
                "position_id": position_id or fill.order_id,
                "entry_price": fill.price,
                "average_entry_price": average_entry,
                "filled_units": filled_units,
                "units": record.units or fill.units,
            },
            price=fill.price,
            units=fill.units,
            broker_order_id=fill.order_id,
            client_order_id=fill.client_order_id or record.client_order_id,
            position_id=position_id or fill.order_id,
            metadata=_fill_metadata(fill, metadata),
        )

    def record_partial_close(
        self,
        record: TradeLifecycleRecord,
        *,
        timestamp: pd.Timestamp | None = None,
        price: float,
        units: float,
        pnl: float = 0.0,
        broker_order_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Record a partial close while keeping the lifecycle active."""

        return self._transition(
            record,
            "partially_closed",
            "partial_close",
            timestamp=timestamp,
            reason="partial_close",
            updates={
                "closed_units": record.closed_units + units,
                "exit_price": price,
                "realized_pnl": record.realized_pnl + pnl,
            },
            price=price,
            units=units,
            pnl=pnl,
            broker_order_id=broker_order_id,
            metadata=metadata,
        )

    def record_close(
        self,
        record: TradeLifecycleRecord,
        *,
        timestamp: pd.Timestamp | None = None,
        fill: OrderFill | None = None,
        price: float | None = None,
        units: float | None = None,
        pnl: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Mark an open lifecycle as fully closed."""

        close_price = fill.price if fill is not None else price
        close_units = float(fill.units) if fill is not None else float(units or 0.0)
        broker_order_id = fill.order_id if fill is not None else None
        ts = pd.Timestamp(fill.timestamp) if fill is not None else timestamp
        return self._transition(
            record,
            "closed",
            "closed",
            timestamp=ts,
            reason="position_closed",
            updates={
                "closed_units": record.closed_units + close_units,
                "exit_price": close_price,
                "realized_pnl": record.realized_pnl + pnl,
            },
            price=close_price,
            units=close_units,
            pnl=pnl,
            broker_order_id=broker_order_id,
            metadata=_fill_metadata(fill, metadata) if fill is not None else metadata,
        )

    def cancel(
        self,
        record: TradeLifecycleRecord,
        reason: str,
        *,
        timestamp: pd.Timestamp | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Cancel a non-filled lifecycle."""

        return self._transition(
            record,
            "cancelled",
            "cancelled",
            timestamp=timestamp,
            reason=reason,
            metadata=metadata,
        )

    def fail(
        self,
        record: TradeLifecycleRecord,
        reason: str,
        *,
        timestamp: pd.Timestamp | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Mark a lifecycle as failed."""

        reasons = tuple(dict.fromkeys((*record.reasons, reason)))
        return self._transition(
            record,
            "failed",
            "failed",
            timestamp=timestamp,
            reason=reason,
            updates={"reasons": reasons},
            metadata=metadata,
        )

    def note(
        self,
        record: TradeLifecycleRecord,
        note: str,
        *,
        timestamp: pd.Timestamp | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TradeLifecycleRecord:
        """Append a note without changing state."""

        return self._transition(
            record,
            record.state,
            "note",
            timestamp=timestamp,
            reason=note,
            metadata=metadata,
            allow_same_state=True,
        )

    def to_frame(self, record: TradeLifecycleRecord) -> pd.DataFrame:
        """Return lifecycle history as a DataFrame."""

        return pd.DataFrame([event.to_dict() for event in record.history])

    def _transition(
        self,
        record: TradeLifecycleRecord,
        new_state: LifecycleState,
        event_type: LifecycleEventType,
        *,
        timestamp: pd.Timestamp | None,
        reason: str | None,
        updates: dict[str, Any] | None = None,
        price: float | None = None,
        units: float | None = None,
        pnl: float | None = None,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        position_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_same_state: bool = False,
    ) -> TradeLifecycleRecord:
        if new_state != record.state or not allow_same_state:
            self._validate_transition(record.state, new_state)
        ts = utc_timestamp(timestamp)
        event = TradeLifecycleEvent(
            timestamp=ts,
            event_type=event_type,
            state_from=record.state,
            state_to=new_state,
            reason=reason,
            price=price,
            units=units,
            pnl=pnl,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            position_id=position_id,
            metadata=dict(metadata or {}),
        )
        return replace(
            record,
            state=new_state,
            updated_at=ts,
            history=(*record.history, event),
            **dict(updates or {}),
        )

    @staticmethod
    def _validate_transition(current: LifecycleState, new_state: LifecycleState) -> None:
        if new_state not in ALLOWED_TRANSITIONS[current]:
            raise TradeLifecycleError(f"invalid lifecycle transition: {current} -> {new_state}")


def _split_reasons(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(part) for part in value if str(part))
    if pd.isna(value):
        return ()
    if isinstance(value, str):
        return tuple(part for part in value.split(";") if part)
    return (str(value),)


def _optional_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _order_updates(order: OrderRequest) -> dict[str, Any]:
    return {
        "client_order_id": order.client_order_id,
        "units": order.units,
        "stop_loss": order.stop_loss,
        "take_profit": order.take_profit,
        "metadata": {**order.metadata},
    }


def _fill_metadata(fill: OrderFill, metadata: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "spread": fill.spread,
        "slippage": fill.slippage,
        "commission": fill.commission,
        **dict(metadata or {}),
    }
