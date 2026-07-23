"""Trade lifecycle models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal
from uuid import uuid4

import pandas as pd

LifecycleState = Literal[
    "created",
    "signal",
    "approved",
    "blocked",
    "submitted",
    "open",
    "partially_closed",
    "closed",
    "cancelled",
    "failed",
]
LifecycleEventType = Literal[
    "created",
    "signal",
    "approved",
    "blocked",
    "submitted",
    "filled",
    "partial_close",
    "closed",
    "cancelled",
    "failed",
    "note",
]

TERMINAL_STATES: frozenset[LifecycleState] = frozenset({"blocked", "closed", "cancelled", "failed"})


def lifecycle_id() -> str:
    """Return a stable local lifecycle ID."""

    return uuid4().hex


def utc_timestamp(value: object | None = None) -> pd.Timestamp:
    """Normalize a timestamp-like value to UTC."""

    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


@dataclass(frozen=True)
class TradeLifecycleEvent:
    """One transition or note in a trade lifecycle."""

    timestamp: pd.Timestamp
    event_type: LifecycleEventType
    state_from: LifecycleState
    state_to: LifecycleState
    reason: str | None = None
    price: float | None = None
    units: float | None = None
    pnl: float | None = None
    broker_order_id: str | None = None
    client_order_id: str | None = None
    position_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        record["timestamp"] = utc_timestamp(self.timestamp).isoformat()
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "TradeLifecycleEvent":
        return cls(
            timestamp=utc_timestamp(record["timestamp"]),
            event_type=record["event_type"],
            state_from=record["state_from"],
            state_to=record["state_to"],
            reason=record.get("reason"),
            price=record.get("price"),
            units=record.get("units"),
            pnl=record.get("pnl"),
            broker_order_id=record.get("broker_order_id"),
            client_order_id=record.get("client_order_id"),
            position_id=record.get("position_id"),
            metadata=dict(record.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TradeLifecycleRecord:
    """Current state and full event history for one trade attempt."""

    trade_id: str
    symbol: str
    side: str
    state: LifecycleState
    created_at: pd.Timestamp
    updated_at: pd.Timestamp
    signal_timestamp: pd.Timestamp | None = None
    setup_name: str = "none"
    confidence: float | None = None
    client_order_id: str | None = None
    broker_order_id: str | None = None
    position_id: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    units: float | None = None
    filled_units: float = 0.0
    closed_units: float = 0.0
    average_entry_price: float | None = None
    exit_price: float | None = None
    realized_pnl: float = 0.0
    reasons: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    history: tuple[TradeLifecycleEvent, ...] = ()

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return not self.is_terminal

    def to_dict(self) -> dict[str, Any]:
        record = asdict(self)
        for key in ("created_at", "updated_at", "signal_timestamp"):
            if record[key] is not None:
                record[key] = utc_timestamp(record[key]).isoformat()
        record["history"] = [event.to_dict() for event in self.history]
        record["reasons"] = list(self.reasons)
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> "TradeLifecycleRecord":
        return cls(
            trade_id=record["trade_id"],
            symbol=str(record["symbol"]).upper(),
            side=str(record["side"]),
            state=record["state"],
            created_at=utc_timestamp(record["created_at"]),
            updated_at=utc_timestamp(record["updated_at"]),
            signal_timestamp=utc_timestamp(record["signal_timestamp"]) if record.get("signal_timestamp") else None,
            setup_name=str(record.get("setup_name") or "none"),
            confidence=record.get("confidence"),
            client_order_id=record.get("client_order_id"),
            broker_order_id=record.get("broker_order_id"),
            position_id=record.get("position_id"),
            entry_price=record.get("entry_price"),
            stop_loss=record.get("stop_loss"),
            take_profit=record.get("take_profit"),
            units=record.get("units"),
            filled_units=float(record.get("filled_units") or 0.0),
            closed_units=float(record.get("closed_units") or 0.0),
            average_entry_price=record.get("average_entry_price"),
            exit_price=record.get("exit_price"),
            realized_pnl=float(record.get("realized_pnl") or 0.0),
            reasons=tuple(record.get("reasons") or ()),
            metadata=dict(record.get("metadata") or {}),
            history=tuple(TradeLifecycleEvent.from_dict(event) for event in record.get("history", [])),
        )
