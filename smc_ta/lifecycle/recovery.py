"""Broker-synchronized lifecycle recovery after process restart."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import OrderFill, OrderRequest, Position, utc_now
from smc_ta.lifecycle.models import TradeLifecycleEvent, TradeLifecycleRecord
from smc_ta.lifecycle.state_machine import TradeLifecycleStateMachine
from smc_ta.lifecycle.store import TradeLifecycleStore
from smc_ta.reconciliation.sync import RestartSyncReport


@dataclass(frozen=True)
class LifecycleRecoveryConfig:
    """Controls lifecycle recovery repairs after broker restart sync."""

    create_missing_lifecycles_for_broker_positions: bool = False
    mark_missing_broker_positions_closed: bool = False
    fail_unfilled_lifecycles_without_broker_position: bool = False
    update_matched_lifecycles_from_broker: bool = True
    open_matched_unfilled_lifecycles_from_broker: bool = True
    match_unlinked_records_by_symbol_side: bool = False
    block_on_untracked_broker_positions: bool = True
    block_on_missing_broker_positions: bool = True
    block_on_unfilled_lifecycle_without_broker_position: bool = True
    block_on_duplicate_lifecycle_positions: bool = True
    recovered_setup_name: str = "broker_recovered_after_restart"


@dataclass(frozen=True)
class LifecycleRecoveryAction:
    """One lifecycle recovery action or startup finding."""

    action: str
    severity: str
    message: str
    symbol: str | None = None
    trade_id: str | None = None
    position_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class LifecycleRecoveryReport:
    """Full lifecycle recovery report."""

    actions: tuple[LifecycleRecoveryAction, ...]
    broker_positions: tuple[Position, ...]
    before_records: tuple[TradeLifecycleRecord, ...]
    after_records: tuple[TradeLifecycleRecord, ...]
    checked_at: datetime = field(default_factory=utc_now)

    @property
    def ok(self) -> bool:
        return not any(action.blocking for action in self.actions)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(action.action for action in self.actions if action.blocking)

    def summary(self) -> str:
        if self.ok:
            return "lifecycle_recovery_ok"
        return ";".join(self.blocking_reasons)

    def to_frame(self) -> pd.DataFrame:
        """Return lifecycle recovery actions as a DataFrame."""

        return pd.DataFrame([asdict(action) for action in self.actions])

    def records_frame(self) -> pd.DataFrame:
        """Return recovered lifecycle records as a compact DataFrame."""

        return lifecycle_records_frame(self.after_records)


def recover_lifecycle_after_restart(
    broker: BrokerAdapter,
    lifecycle_store: TradeLifecycleStore,
    *,
    symbol: str | None = None,
    config: LifecycleRecoveryConfig | None = None,
    restart_report: RestartSyncReport | None = None,
) -> LifecycleRecoveryReport:
    """Recover active lifecycle records from broker-open positions.

    This should run after broker position-ledger restart sync and before
    preflight/bot loops resume. It does not place or close broker orders.
    """

    cfg = config or LifecycleRecoveryConfig()
    symbol_filter = symbol.upper() if symbol else None
    broker_positions = _broker_positions(broker, symbol=symbol_filter, restart_report=restart_report)
    before = tuple(lifecycle_store.list_records(symbol=symbol_filter))
    active_records = tuple(record for record in before if record.is_active)
    actions: list[LifecycleRecoveryAction] = []

    position_by_id = {position.position_id: position for position in broker_positions}
    records_by_position = _records_by_position(active_records, broker_positions)
    matched_record_ids: set[str] = set()
    matched_position_ids: set[str] = set()

    _duplicate_lifecycle_actions(records_by_position, cfg, actions)

    for position_id, records in records_by_position.items():
        position = position_by_id.get(position_id)
        if position is None:
            continue
        for record in records:
            recovered = _recover_matched_record(record, position, cfg, actions)
            if recovered is not None:
                lifecycle_store.save(recovered)
                matched_record_ids.add(record.trade_id)
                matched_position_ids.add(position.position_id)

    if cfg.match_unlinked_records_by_symbol_side:
        _match_unlinked_by_symbol_side(
            active_records,
            broker_positions,
            matched_record_ids,
            matched_position_ids,
            lifecycle_store,
            cfg,
            actions,
        )

    refreshed = tuple(lifecycle_store.list_records(symbol=symbol_filter))
    active_after_match = tuple(record for record in refreshed if record.is_active)
    active_by_id = {record.trade_id: record for record in active_after_match}
    matched_record_ids &= set(active_by_id)
    broker_ids = {position.position_id for position in broker_positions}

    for record in active_after_match:
        if record.trade_id in matched_record_ids:
            continue
        if _record_position_ids(record) & broker_ids:
            continue
        recovered = _recover_record_missing_broker_position(record, cfg, actions)
        if recovered is not None:
            lifecycle_store.save(recovered)

    refreshed = tuple(lifecycle_store.list_records(symbol=symbol_filter))
    active_after_missing = tuple(record for record in refreshed if record.is_active)
    tracked_position_ids = _tracked_position_ids(active_after_missing)
    for position in broker_positions:
        if position.position_id in matched_position_ids or position.position_id in tracked_position_ids:
            continue
        recovered = _recover_untracked_broker_position(position, cfg, actions)
        if recovered is not None:
            lifecycle_store.save(recovered)

    after = tuple(lifecycle_store.list_records(symbol=symbol_filter))
    return LifecycleRecoveryReport(
        actions=tuple(actions),
        broker_positions=tuple(broker_positions),
        before_records=before,
        after_records=after,
    )


def lifecycle_records_frame(records: tuple[TradeLifecycleRecord, ...] | list[TradeLifecycleRecord]) -> pd.DataFrame:
    """Return a compact lifecycle records table."""

    if not records:
        return pd.DataFrame()
    return pd.DataFrame(
        [
            {
                "trade_id": record.trade_id,
                "symbol": record.symbol,
                "side": record.side,
                "state": record.state,
                "created_at": record.created_at,
                "updated_at": record.updated_at,
                "setup_name": record.setup_name,
                "position_id": record.position_id,
                "broker_order_id": record.broker_order_id,
                "entry_price": record.entry_price,
                "stop_loss": record.stop_loss,
                "take_profit": record.take_profit,
                "units": record.units,
                "filled_units": record.filled_units,
                "closed_units": record.closed_units,
                "realized_pnl": record.realized_pnl,
                "reasons": ";".join(record.reasons),
            }
            for record in records
        ]
    )


def report_to_jsonable(report: LifecycleRecoveryReport) -> dict[str, Any]:
    """Return a JSON-friendly lifecycle recovery report dictionary."""

    return {
        "ok": report.ok,
        "summary": report.summary(),
        "checked_at": pd.Timestamp(report.checked_at).isoformat(),
        "actions": [_jsonable(asdict(action)) for action in report.actions],
        "broker_positions": [
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "units": position.units,
                "entry_price": position.entry_price,
            }
            for position in report.broker_positions
        ],
        "before_records": len(report.before_records),
        "after_records": len(report.after_records),
        "active_after_records": sum(1 for record in report.after_records if record.is_active),
    }


def write_lifecycle_recovery_report(report: LifecycleRecoveryReport, path: str | Path) -> Path:
    """Write a JSON lifecycle recovery report to disk."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report_to_jsonable(report), indent=2, sort_keys=True), encoding="utf-8")
    return output


def _broker_positions(
    broker: BrokerAdapter,
    *,
    symbol: str | None,
    restart_report: RestartSyncReport | None,
) -> tuple[Position, ...]:
    if restart_report is not None:
        positions = restart_report.after_reconciliation.broker_positions
        if symbol is None:
            return tuple(positions)
        return tuple(position for position in positions if position.symbol == symbol.upper())
    return tuple(broker.get_open_positions(symbol))


def _records_by_position(
    records: tuple[TradeLifecycleRecord, ...],
    positions: tuple[Position, ...],
) -> dict[str, list[TradeLifecycleRecord]]:
    broker_ids = {position.position_id for position in positions}
    broker_trade_ids = {position.position_id: _broker_position_ids(position) for position in positions}
    out: dict[str, list[TradeLifecycleRecord]] = {}
    for record in records:
        record_ids = _record_position_ids(record)
        matched = record_ids & broker_ids
        if not matched:
            for position_id, ids in broker_trade_ids.items():
                if record_ids & ids:
                    matched.add(position_id)
        for position_id in matched:
            out.setdefault(position_id, []).append(record)
    return out


def _duplicate_lifecycle_actions(
    records_by_position: dict[str, list[TradeLifecycleRecord]],
    config: LifecycleRecoveryConfig,
    actions: list[LifecycleRecoveryAction],
) -> None:
    if not config.block_on_duplicate_lifecycle_positions:
        return
    for position_id, records in records_by_position.items():
        if len(records) <= 1:
            continue
        actions.append(
            LifecycleRecoveryAction(
                action="duplicate_lifecycle_position",
                severity="blocking",
                message="multiple active lifecycle records reference the same broker position",
                position_id=position_id,
                details={"trade_ids": [record.trade_id for record in records]},
            )
        )


def _recover_matched_record(
    record: TradeLifecycleRecord,
    position: Position,
    config: LifecycleRecoveryConfig,
    actions: list[LifecycleRecoveryAction],
) -> TradeLifecycleRecord | None:
    if record.state in {"signal", "approved", "submitted"} and config.open_matched_unfilled_lifecycles_from_broker:
        recovered = _open_record_from_broker_position(record, position)
        actions.append(
            LifecycleRecoveryAction(
                action="open_lifecycle_from_broker_position",
                severity="warning",
                message="active unfilled lifecycle was opened from matching broker position after restart",
                symbol=position.symbol,
                trade_id=record.trade_id,
                position_id=position.position_id,
                details={"previous_state": record.state, "units": position.units, "entry_price": position.entry_price},
            )
        )
        return recovered

    if record.state in {"open", "partially_closed"} and config.update_matched_lifecycles_from_broker:
        recovered = _sync_open_record_from_broker_position(record, position)
        actions.append(
            LifecycleRecoveryAction(
                action="sync_lifecycle_from_broker_position",
                severity="info",
                message="active lifecycle matched broker position and was synchronized",
                symbol=position.symbol,
                trade_id=record.trade_id,
                position_id=position.position_id,
                details={"state": record.state, "units": position.units, "entry_price": position.entry_price},
            )
        )
        return recovered

    actions.append(
        LifecycleRecoveryAction(
            action="lifecycle_position_match",
            severity="info",
            message="active lifecycle matched broker position",
            symbol=position.symbol,
            trade_id=record.trade_id,
            position_id=position.position_id,
            details={"state": record.state},
        )
    )
    return record


def _match_unlinked_by_symbol_side(
    active_records: tuple[TradeLifecycleRecord, ...],
    broker_positions: tuple[Position, ...],
    matched_record_ids: set[str],
    matched_position_ids: set[str],
    lifecycle_store: TradeLifecycleStore,
    config: LifecycleRecoveryConfig,
    actions: list[LifecycleRecoveryAction],
) -> None:
    unmatched_records = [
        record
        for record in active_records
        if record.trade_id not in matched_record_ids and not _record_position_ids(record)
    ]
    unmatched_positions = [position for position in broker_positions if position.position_id not in matched_position_ids]
    for record in unmatched_records:
        candidates = [
            position
            for position in unmatched_positions
            if position.symbol == record.symbol and position.side == _record_position_side(record)
        ]
        if len(candidates) != 1:
            continue
        position = candidates[0]
        recovered = _recover_matched_record(record, position, config, actions)
        if recovered is None:
            continue
        recovered = _sync_open_record_from_broker_position(recovered, position, reason="matched_by_symbol_side_after_restart")
        lifecycle_store.save(recovered)
        matched_record_ids.add(record.trade_id)
        matched_position_ids.add(position.position_id)
        actions.append(
            LifecycleRecoveryAction(
                action="match_lifecycle_by_symbol_side",
                severity="warning",
                message="unlinked lifecycle matched exactly one broker position by symbol and side",
                symbol=position.symbol,
                trade_id=record.trade_id,
                position_id=position.position_id,
            )
        )


def _recover_record_missing_broker_position(
    record: TradeLifecycleRecord,
    config: LifecycleRecoveryConfig,
    actions: list[LifecycleRecoveryAction],
) -> TradeLifecycleRecord | None:
    if record.state in {"open", "partially_closed"}:
        if config.mark_missing_broker_positions_closed:
            recovered = _close_missing_broker_position_record(record)
            actions.append(
                LifecycleRecoveryAction(
                    action="mark_lifecycle_closed_missing_broker_position",
                    severity="warning",
                    message="active lifecycle had no broker position and was marked closed",
                    symbol=record.symbol,
                    trade_id=record.trade_id,
                    position_id=record.position_id,
                    details={"previous_state": record.state},
                )
            )
            return recovered
        severity = "blocking" if config.block_on_missing_broker_positions else "warning"
        actions.append(
            LifecycleRecoveryAction(
                action="lifecycle_missing_broker_position",
                severity=severity,
                message="active open lifecycle has no matching broker position",
                symbol=record.symbol,
                trade_id=record.trade_id,
                position_id=record.position_id,
                details={"state": record.state},
            )
        )
        return None

    if config.fail_unfilled_lifecycles_without_broker_position:
        recovered = TradeLifecycleStateMachine().fail(
            record,
            "broker_position_missing_after_restart",
            metadata={"source": "lifecycle_recovery"},
        )
        actions.append(
            LifecycleRecoveryAction(
                action="fail_unfilled_lifecycle_without_broker_position",
                severity="warning",
                message="unfilled active lifecycle had no broker position and was marked failed",
                symbol=record.symbol,
                trade_id=record.trade_id,
                details={"previous_state": record.state},
            )
        )
        return recovered

    severity = "blocking" if config.block_on_unfilled_lifecycle_without_broker_position else "warning"
    actions.append(
        LifecycleRecoveryAction(
            action="unfilled_lifecycle_without_broker_position",
            severity=severity,
            message="unfilled active lifecycle has no matching broker position",
            symbol=record.symbol,
            trade_id=record.trade_id,
            details={"state": record.state},
        )
    )
    return None


def _recover_untracked_broker_position(
    position: Position,
    config: LifecycleRecoveryConfig,
    actions: list[LifecycleRecoveryAction],
) -> TradeLifecycleRecord | None:
    if config.create_missing_lifecycles_for_broker_positions:
        recovered = _create_record_from_broker_position(position, setup_name=config.recovered_setup_name)
        actions.append(
            LifecycleRecoveryAction(
                action="create_lifecycle_from_broker_position",
                severity="warning",
                message="broker position had no active lifecycle and a recovery lifecycle was created",
                symbol=position.symbol,
                trade_id=recovered.trade_id,
                position_id=position.position_id,
                details={"side": position.side, "units": position.units, "entry_price": position.entry_price},
            )
        )
        return recovered

    severity = "blocking" if config.block_on_untracked_broker_positions else "warning"
    actions.append(
        LifecycleRecoveryAction(
            action="untracked_broker_position_lifecycle",
            severity=severity,
            message="broker position has no active lifecycle record",
            symbol=position.symbol,
            position_id=position.position_id,
            details={"side": position.side, "units": position.units, "entry_price": position.entry_price},
        )
    )
    return None


def _create_record_from_broker_position(position: Position, *, setup_name: str) -> TradeLifecycleRecord:
    machine = TradeLifecycleStateMachine()
    signal = _recovery_signal(position)
    record = machine.create_from_signal(
        symbol=position.symbol,
        timestamp=pd.Timestamp(position.opened_at),
        signal=signal,
        setup_name=setup_name,
        trade_id=f"recovered_{position.position_id}",
        metadata={"source": "lifecycle_recovery", "broker_position_metadata": dict(position.metadata)},
    )
    order = _recovery_order(position)
    record = machine.approve(record, order=order, reason="broker_position_recovered_after_restart")
    record = machine.submit(record, order)
    return machine.record_fill(
        record,
        _recovery_fill(position, order),
        position_id=position.position_id,
        metadata={"source": "lifecycle_recovery"},
    )


def _open_record_from_broker_position(record: TradeLifecycleRecord, position: Position) -> TradeLifecycleRecord:
    machine = TradeLifecycleStateMachine()
    order = _recovery_order(position, client_order_id=record.client_order_id)
    recovered = record
    if recovered.state == "signal":
        recovered = machine.approve(
            recovered,
            order=order,
            reason="broker_position_recovered_after_restart",
            metadata={"source": "lifecycle_recovery"},
        )
    if recovered.state == "approved":
        recovered = machine.submit(recovered, order, metadata={"source": "lifecycle_recovery"})
    if recovered.state == "submitted":
        recovered = machine.record_fill(
            recovered,
            _recovery_fill(position, order),
            position_id=position.position_id,
            metadata={"source": "lifecycle_recovery"},
        )
    return _sync_open_record_from_broker_position(recovered, position)


def _sync_open_record_from_broker_position(
    record: TradeLifecycleRecord,
    position: Position,
    *,
    reason: str = "lifecycle_synced_with_broker_position_after_restart",
) -> TradeLifecycleRecord:
    metadata = {
        **dict(record.metadata),
        "lifecycle_recovery": {
            "action": reason,
            "synced_at": pd.Timestamp.now(tz="UTC").isoformat(),
            "broker_position_metadata": dict(position.metadata),
        },
    }
    event = TradeLifecycleEvent(
        timestamp=pd.Timestamp.now(tz="UTC"),
        event_type="note",
        state_from=record.state,
        state_to=record.state,
        reason=reason,
        price=position.entry_price,
        units=position.units,
        position_id=position.position_id,
        metadata={"source": "lifecycle_recovery"},
    )
    return replace(
        record,
        updated_at=event.timestamp,
        position_id=position.position_id,
        entry_price=position.entry_price,
        average_entry_price=position.entry_price,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        units=position.units,
        filled_units=max(record.filled_units, position.units),
        metadata=metadata,
        history=(*record.history, event),
    )


def _close_missing_broker_position_record(record: TradeLifecycleRecord) -> TradeLifecycleRecord:
    close_units = max(0.0, float(record.filled_units or record.units or 0.0) - float(record.closed_units or 0.0))
    return TradeLifecycleStateMachine().record_close(
        record,
        timestamp=pd.Timestamp.now(tz="UTC"),
        price=record.exit_price,
        units=close_units,
        pnl=0.0,
        metadata={"source": "lifecycle_recovery", "reason": "broker_position_missing_after_restart"},
    )


def _recovery_signal(position: Position) -> pd.Series:
    side = "long" if position.side == "long" else "short"
    return pd.Series(
        {
            "side": side,
            "confidence": 0.0,
            "entry_reference": position.entry_price,
            "stop_reference": position.stop_loss,
            "target_reference": position.take_profit,
            "reference_rr": None,
            "long_score": 0.0,
            "short_score": 0.0,
            "reasons": "broker_position_recovered_after_restart",
        }
    )


def _recovery_order(position: Position, *, client_order_id: str | None = None) -> OrderRequest:
    order = OrderRequest(
        symbol=position.symbol,
        side="buy" if position.side == "long" else "sell",
        units=position.units,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        metadata={"source": "lifecycle_recovery", "broker_position_id": position.position_id},
    )
    if client_order_id is None:
        return order
    return replace(order, client_order_id=client_order_id)


def _recovery_fill(position: Position, order: OrderRequest) -> OrderFill:
    return OrderFill(
        order_id=position.position_id,
        symbol=position.symbol,
        side="buy" if position.side == "long" else "sell",
        units=position.units,
        price=position.entry_price,
        spread=0.0,
        slippage=0.0,
        commission=0.0,
        timestamp=position.opened_at,
        client_order_id=order.client_order_id,
        metadata={"source": "lifecycle_recovery", "broker_position_metadata": dict(position.metadata)},
    )


def _record_position_side(record: TradeLifecycleRecord) -> str:
    return "long" if record.side == "long" else "short"


def _record_position_ids(record: TradeLifecycleRecord) -> set[str]:
    ids = {str(value) for value in (record.position_id, record.broker_order_id) if value}
    metadata_position_id = record.metadata.get("broker_position_id")
    if metadata_position_id:
        ids.add(str(metadata_position_id))
    return ids


def _broker_position_ids(position: Position) -> set[str]:
    ids = {position.position_id}
    raw_trade_ids = position.metadata.get("oanda_trade_ids", ())
    if isinstance(raw_trade_ids, str):
        ids.add(raw_trade_ids)
    else:
        ids.update(str(item) for item in raw_trade_ids)
    return ids


def _tracked_position_ids(records: tuple[TradeLifecycleRecord, ...]) -> set[str]:
    ids: set[str] = set()
    for record in records:
        ids.update(_record_position_ids(record))
    return ids


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
