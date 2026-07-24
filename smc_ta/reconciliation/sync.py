"""Restart recovery and broker transaction sync helpers."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import BrokerOrder, Position, utc_now
from smc_ta.reconciliation.ledger import PositionLedger
from smc_ta.reconciliation.models import ReconciliationConfig, ReconciliationResult
from smc_ta.reconciliation.service import BrokerReconciler


class SyncCheckpointStore(Protocol):
    """Persistent store for broker transaction checkpoints."""

    def get_checkpoint(self, name: str) -> str | None:
        """Return the last observed checkpoint value."""

    def set_checkpoint(self, name: str, value: str) -> None:
        """Persist the latest observed checkpoint value."""


class MemorySyncCheckpointStore:
    """In-memory broker transaction checkpoint store."""

    def __init__(self, checkpoints: dict[str, str] | None = None) -> None:
        self.checkpoints = dict(checkpoints or {})

    def get_checkpoint(self, name: str) -> str | None:
        return self.checkpoints.get(name)

    def set_checkpoint(self, name: str, value: str) -> None:
        self.checkpoints[name] = str(value)


class SQLiteSyncCheckpointStore:
    """SQLite broker transaction checkpoint store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sync_checkpoints (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get_checkpoint(self, name: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM sync_checkpoints WHERE name = ?", (name,)).fetchone()
        return str(row[0]) if row else None

    def set_checkpoint(self, name: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sync_checkpoints (name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (name, str(value), pd.Timestamp.now(tz="UTC").isoformat()),
            )


@dataclass(frozen=True)
class RestartSyncConfig:
    """Controls how startup broker-state recovery repairs local state."""

    adopt_unmanaged_broker_positions: bool = False
    mark_missing_expected_positions_closed: bool = False
    update_mismatched_expected_positions: bool = False
    fetch_broker_transactions: bool = True
    fetch_pending_orders: bool = True
    block_on_unlinked_pending_orders: bool = True
    max_adopted_positions: int = 10
    checkpoint_name: str = "broker_transaction_id"


@dataclass(frozen=True)
class RestartSyncAction:
    """One audit action or blocking finding from restart sync."""

    action: str
    severity: str
    message: str
    symbol: str | None = None
    position_id: str | None = None
    order_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class RestartSyncReport:
    """Full restart sync report."""

    before_reconciliation: ReconciliationResult
    after_reconciliation: ReconciliationResult
    actions: tuple[RestartSyncAction, ...]
    pending_orders: tuple[BrokerOrder, ...] = ()
    transactions: tuple[dict[str, Any], ...] = ()
    account_changes: dict[str, Any] = field(default_factory=dict)
    previous_transaction_id: str | None = None
    latest_transaction_id: str | None = None
    checked_at: datetime = field(default_factory=utc_now)

    @property
    def ok(self) -> bool:
        return self.after_reconciliation.ok and not any(action.blocking for action in self.actions)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons = list(self.after_reconciliation.blocking_reasons)
        reasons.extend(action.action for action in self.actions if action.blocking)
        return tuple(reasons)

    def summary(self) -> str:
        if self.ok:
            return "restart_sync_ok"
        return ";".join(self.blocking_reasons)

    def to_frame(self) -> pd.DataFrame:
        """Return sync actions as a DataFrame."""

        return pd.DataFrame([asdict(action) for action in self.actions])

    def orders_frame(self) -> pd.DataFrame:
        """Return pending broker orders as a DataFrame."""

        return pd.DataFrame([asdict(order) for order in self.pending_orders])

    def transactions_frame(self) -> pd.DataFrame:
        """Return broker transactions observed since the previous checkpoint."""

        return pd.DataFrame(list(self.transactions))


def sync_broker_state_after_restart(
    broker: BrokerAdapter,
    ledger: PositionLedger,
    *,
    symbol: str | None = None,
    config: RestartSyncConfig | None = None,
    reconciliation_config: ReconciliationConfig | None = None,
    checkpoint_store: SyncCheckpointStore | None = None,
) -> RestartSyncReport:
    """Reconcile broker state after process restart and optionally repair the ledger.

    Safe defaults are report-only. Set the explicit config flags to adopt live
    broker positions, close ledger-only positions, or update mismatched expected
    positions after you have reviewed the startup report.
    """

    cfg = config or RestartSyncConfig()
    reconciler = BrokerReconciler(ledger, reconciliation_config)
    previous_transaction_id: str | None = None
    latest_transaction_id: str | None = None
    account_changes: dict[str, Any] = {}
    transactions: tuple[dict[str, Any], ...] = ()
    actions: list[RestartSyncAction] = []

    if cfg.fetch_broker_transactions:
        previous_transaction_id, latest_transaction_id, account_changes, transactions = _sync_transaction_checkpoint(
            broker,
            checkpoint_store=checkpoint_store,
            checkpoint_name=cfg.checkpoint_name,
            actions=actions,
        )

    pending_orders = _fetch_pending_orders(broker, symbol=symbol, enabled=cfg.fetch_pending_orders, actions=actions)

    before = reconciler.reconcile_broker(broker, symbol)
    broker_by_id = {position.position_id: position for position in before.broker_positions}
    expected_by_id = {position.position_id: position for position in before.expected_positions}

    _adopt_unmanaged_positions(before, broker_by_id, ledger, cfg, actions)
    _mark_missing_expected_positions(before, expected_by_id, ledger, cfg, actions)
    _update_mismatched_positions(before, broker_by_id, ledger, cfg, actions)

    after = reconciler.reconcile_broker(broker, symbol)
    actions.extend(_pending_order_actions(pending_orders, after.broker_positions, cfg))

    return RestartSyncReport(
        before_reconciliation=before,
        after_reconciliation=after,
        actions=tuple(actions),
        pending_orders=tuple(pending_orders),
        transactions=transactions,
        account_changes=account_changes,
        previous_transaction_id=previous_transaction_id,
        latest_transaction_id=latest_transaction_id,
    )


def _sync_transaction_checkpoint(
    broker: BrokerAdapter,
    *,
    checkpoint_store: SyncCheckpointStore | None,
    checkpoint_name: str,
    actions: list[RestartSyncAction],
) -> tuple[str | None, str | None, dict[str, Any], tuple[dict[str, Any], ...]]:
    previous = checkpoint_store.get_checkpoint(checkpoint_name) if checkpoint_store is not None else None
    changes: dict[str, Any] = {}
    latest: str | None = None
    transactions: tuple[dict[str, Any], ...] = ()

    try:
        if previous and hasattr(broker, "get_account_changes"):
            changes = getattr(broker, "get_account_changes")(previous)
            latest = _optional_str(changes.get("lastTransactionID"))
            raw_transactions = changes.get("changes", {}).get("transactions", [])
            transactions = tuple(dict(item) for item in raw_transactions if isinstance(item, dict))
            actions.append(
                RestartSyncAction(
                    action="broker_transactions_loaded",
                    severity="info",
                    message="broker account changes loaded since previous transaction checkpoint",
                    details={"previous_transaction_id": previous, "transactions": len(transactions)},
                )
            )
        elif hasattr(broker, "get_latest_transaction_id"):
            latest = getattr(broker, "get_latest_transaction_id")()
            actions.append(
                RestartSyncAction(
                    action="broker_transaction_checkpoint_loaded",
                    severity="info",
                    message="latest broker transaction checkpoint loaded",
                    details={"latest_transaction_id": latest},
                )
            )
        elif checkpoint_store is not None:
            actions.append(
                RestartSyncAction(
                    action="broker_transaction_sync_unavailable",
                    severity="warning",
                    message="broker adapter does not expose transaction checkpoint methods",
                )
            )
    except Exception as exc:
        actions.append(
            RestartSyncAction(
                action="broker_transaction_sync_failed",
                severity="blocking",
                message=str(exc),
                details={"exception_type": type(exc).__name__},
            )
        )

    if latest and checkpoint_store is not None:
        checkpoint_store.set_checkpoint(checkpoint_name, latest)
        actions.append(
            RestartSyncAction(
                action="transaction_checkpoint_saved",
                severity="info",
                message="latest broker transaction checkpoint saved",
                details={"checkpoint_name": checkpoint_name, "latest_transaction_id": latest},
            )
        )
    return previous, latest, changes, transactions


def _fetch_pending_orders(
    broker: BrokerAdapter,
    *,
    symbol: str | None,
    enabled: bool,
    actions: list[RestartSyncAction],
) -> tuple[BrokerOrder, ...]:
    if not enabled:
        return ()
    if not hasattr(broker, "get_pending_orders"):
        return ()
    try:
        pending = getattr(broker, "get_pending_orders")(symbol)
        return tuple(_coerce_broker_order(order) for order in pending)
    except Exception as exc:
        actions.append(
            RestartSyncAction(
                action="pending_order_sync_failed",
                severity="blocking",
                message=str(exc),
                details={"exception_type": type(exc).__name__},
            )
        )
        return ()


def _adopt_unmanaged_positions(
    result: ReconciliationResult,
    broker_by_id: dict[str, Position],
    ledger: PositionLedger,
    config: RestartSyncConfig,
    actions: list[RestartSyncAction],
) -> None:
    unmanaged_ids = [
        str(issue.broker_position_id)
        for issue in result.issues
        if issue.kind == "unmanaged_broker_position" and issue.broker_position_id
    ]
    if not unmanaged_ids:
        return
    if not config.adopt_unmanaged_broker_positions:
        return
    if len(unmanaged_ids) > config.max_adopted_positions:
        actions.append(
            RestartSyncAction(
                action="adopt_broker_positions_refused",
                severity="blocking",
                message="too many unmanaged broker positions to adopt automatically",
                details={"positions": len(unmanaged_ids), "max_adopted_positions": config.max_adopted_positions},
            )
        )
        return
    for position_id in dict.fromkeys(unmanaged_ids):
        position = broker_by_id[position_id]
        synced = _synced_position(position, action="adopted_after_restart")
        ledger.record_open_position(synced)
        actions.append(
            RestartSyncAction(
                action="adopt_broker_position",
                severity="info",
                message="broker position adopted into expected ledger after restart",
                symbol=synced.symbol,
                position_id=synced.position_id,
                details={"units": synced.units, "side": synced.side, "entry_price": synced.entry_price},
            )
        )


def _mark_missing_expected_positions(
    result: ReconciliationResult,
    expected_by_id: dict[str, Position],
    ledger: PositionLedger,
    config: RestartSyncConfig,
    actions: list[RestartSyncAction],
) -> None:
    if not config.mark_missing_expected_positions_closed:
        return
    missing_ids = [
        str(issue.expected_position_id)
        for issue in result.issues
        if issue.kind == "missing_broker_position" and issue.expected_position_id
    ]
    for position_id in dict.fromkeys(missing_ids):
        expected = expected_by_id[position_id]
        ledger.record_closed_position(position_id, closed_at=utc_now())
        actions.append(
            RestartSyncAction(
                action="mark_expected_position_closed",
                severity="warning",
                message="expected ledger position was not open at broker and was marked closed",
                symbol=expected.symbol,
                position_id=position_id,
                details={"side": expected.side, "units": expected.units, "entry_price": expected.entry_price},
            )
        )


def _update_mismatched_positions(
    result: ReconciliationResult,
    broker_by_id: dict[str, Position],
    ledger: PositionLedger,
    config: RestartSyncConfig,
    actions: list[RestartSyncAction],
) -> None:
    if not config.update_mismatched_expected_positions:
        return
    mismatch_kinds = {"symbol_mismatch", "side_mismatch", "units_mismatch", "entry_price_mismatch"}
    mismatch_ids = [
        str(issue.broker_position_id)
        for issue in result.issues
        if issue.kind in mismatch_kinds and issue.broker_position_id in broker_by_id
    ]
    for position_id in dict.fromkeys(mismatch_ids):
        position = _synced_position(broker_by_id[position_id], action="updated_after_restart")
        ledger.record_open_position(position)
        actions.append(
            RestartSyncAction(
                action="update_expected_position_from_broker",
                severity="warning",
                message="expected ledger position was updated from broker state after restart",
                symbol=position.symbol,
                position_id=position.position_id,
                details={"side": position.side, "units": position.units, "entry_price": position.entry_price},
            )
        )


def _pending_order_actions(
    pending_orders: tuple[BrokerOrder, ...],
    broker_positions: tuple[Position, ...],
    config: RestartSyncConfig,
) -> list[RestartSyncAction]:
    trade_ids = _broker_trade_ids(broker_positions)
    actions: list[RestartSyncAction] = []
    for order in pending_orders:
        if order.trade_id and order.trade_id in trade_ids:
            actions.append(
                RestartSyncAction(
                    action="pending_order_linked_to_position",
                    severity="info",
                    message="pending broker order is linked to a synced open position",
                    symbol=order.symbol,
                    position_id=order.trade_id,
                    order_id=order.order_id,
                    details={"order_type": order.order_type, "state": order.state, "price": order.price},
                )
            )
            continue
        severity = "blocking" if config.block_on_unlinked_pending_orders else "warning"
        actions.append(
            RestartSyncAction(
                action="unlinked_pending_order",
                severity=severity,
                message="pending broker order is not linked to a synced open position",
                symbol=order.symbol,
                position_id=order.trade_id,
                order_id=order.order_id,
                details={"order_type": order.order_type, "state": order.state, "price": order.price},
            )
        )
    return actions


def _broker_trade_ids(positions: tuple[Position, ...]) -> set[str]:
    ids: set[str] = set()
    for position in positions:
        ids.add(position.position_id)
        raw_trade_ids = position.metadata.get("oanda_trade_ids", ())
        if isinstance(raw_trade_ids, str):
            ids.add(raw_trade_ids)
        else:
            ids.update(str(item) for item in raw_trade_ids)
    return ids


def _synced_position(position: Position, *, action: str) -> Position:
    synced = deepcopy(position)
    metadata = dict(synced.metadata)
    metadata["restart_sync"] = {
        "action": action,
        "synced_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    synced.metadata = metadata
    return synced


def _coerce_broker_order(value: BrokerOrder | dict[str, Any]) -> BrokerOrder:
    if isinstance(value, BrokerOrder):
        return value
    if not isinstance(value, dict):
        raise TypeError(f"unsupported pending order payload: {type(value).__name__}")
    return BrokerOrder(
        order_id=str(value["order_id"]),
        symbol=_optional_str(value.get("symbol")),
        order_type=str(value.get("order_type", value.get("type", "unknown"))),
        state=str(value.get("state", "unknown")),
        side=value.get("side"),
        units=_optional_float(value.get("units")),
        price=_optional_float(value.get("price")),
        stop_loss=_optional_float(value.get("stop_loss")),
        take_profit=_optional_float(value.get("take_profit")),
        trade_id=_optional_str(value.get("trade_id")),
        created_at=pd.Timestamp(value["created_at"]).to_pydatetime() if value.get("created_at") else None,
        client_order_id=_optional_str(value.get("client_order_id")),
        metadata=dict(value.get("metadata", {})),
    )


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def report_to_jsonable(report: RestartSyncReport) -> dict[str, Any]:
    """Return a JSON-friendly restart sync report dictionary."""

    return {
        "ok": report.ok,
        "summary": report.summary(),
        "previous_transaction_id": report.previous_transaction_id,
        "latest_transaction_id": report.latest_transaction_id,
        "checked_at": pd.Timestamp(report.checked_at).isoformat(),
        "actions": [_jsonable(asdict(action)) for action in report.actions],
        "pending_orders": [_jsonable(asdict(order)) for order in report.pending_orders],
        "transactions": [_jsonable(transaction) for transaction in report.transactions],
        "before_reconciliation": {
            "ok": report.before_reconciliation.ok,
            "blocking_reasons": report.before_reconciliation.blocking_reasons,
            "broker_positions": len(report.before_reconciliation.broker_positions),
            "expected_positions": len(report.before_reconciliation.expected_positions),
        },
        "after_reconciliation": {
            "ok": report.after_reconciliation.ok,
            "blocking_reasons": report.after_reconciliation.blocking_reasons,
            "broker_positions": len(report.after_reconciliation.broker_positions),
            "expected_positions": len(report.after_reconciliation.expected_positions),
        },
    }


def write_restart_sync_report(report: RestartSyncReport, path: str | Path) -> Path:
    """Write a JSON restart sync report to disk."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report_to_jsonable(report), indent=2, sort_keys=True), encoding="utf-8")
    return output


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
