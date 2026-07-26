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
    reconcile_broker_transactions: bool = True
    block_on_rejected_transactions: bool = True
    block_on_cancelled_transactions: bool = False
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
class TransactionReconciliationEvent:
    """One normalized broker transaction event used by restart sync."""

    event: str
    severity: str
    message: str
    transaction_id: str | None = None
    transaction_type: str | None = None
    symbol: str | None = None
    position_id: str | None = None
    order_id: str | None = None
    client_order_id: str | None = None
    timestamp: datetime | None = None
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
    transaction_events: tuple[TransactionReconciliationEvent, ...] = ()
    account_changes: dict[str, Any] = field(default_factory=dict)
    previous_transaction_id: str | None = None
    latest_transaction_id: str | None = None
    checked_at: datetime = field(default_factory=utc_now)

    @property
    def ok(self) -> bool:
        return (
            self.after_reconciliation.ok
            and not any(action.blocking for action in self.actions)
            and not any(event.blocking for event in self.transaction_events)
        )

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons = list(self.after_reconciliation.blocking_reasons)
        reasons.extend(action.action for action in self.actions if action.blocking)
        reasons.extend(event.event for event in self.transaction_events if event.blocking)
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

    def transaction_events_frame(self) -> pd.DataFrame:
        """Return normalized transaction reconciliation events."""

        return pd.DataFrame([asdict(event) for event in self.transaction_events])


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
    transaction_context = _transaction_context(transactions)
    transaction_events: tuple[TransactionReconciliationEvent, ...] = ()
    if cfg.reconcile_broker_transactions and transactions:
        transaction_events = _reconcile_transactions(
            transactions,
            broker_positions=before.broker_positions,
            expected_positions=before.expected_positions,
            config=cfg,
            symbol=symbol,
        )
        actions.extend(_transaction_event_actions(transaction_events))

    broker_by_id = {position.position_id: position for position in before.broker_positions}
    expected_by_id = {position.position_id: position for position in before.expected_positions}

    _adopt_unmanaged_positions(before, broker_by_id, ledger, cfg, actions, transaction_context)
    _mark_missing_expected_positions(before, expected_by_id, ledger, cfg, actions, transaction_context)
    _update_mismatched_positions(before, broker_by_id, ledger, cfg, actions, transaction_context)

    after = reconciler.reconcile_broker(broker, symbol)
    actions.extend(_pending_order_actions(pending_orders, after.broker_positions, cfg))

    return RestartSyncReport(
        before_reconciliation=before,
        after_reconciliation=after,
        actions=tuple(actions),
        pending_orders=tuple(pending_orders),
        transactions=transactions,
        transaction_events=transaction_events,
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

    if previous and hasattr(broker, "get_account_changes"):
        try:
            changes = getattr(broker, "get_account_changes")(previous)
            latest = _optional_str(changes.get("lastTransactionID"))
            transactions = _extract_transactions(changes)
            actions.append(
                RestartSyncAction(
                    action="broker_account_changes_loaded",
                    severity="info",
                    message="broker account changes loaded since previous transaction checkpoint",
                    details={"previous_transaction_id": previous, "transactions": len(transactions)},
                )
            )
        except Exception as exc:
            actions.append(
                RestartSyncAction(
                    action="broker_transaction_sync_failed",
                    severity="blocking",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__, "source": "account_changes"},
                )
            )

    if previous and hasattr(broker, "get_transactions_since"):
        try:
            transaction_response = getattr(broker, "get_transactions_since")(previous)
            direct_transactions = _extract_transactions(transaction_response)
            direct_latest = _optional_str(transaction_response.get("lastTransactionID"))
            if direct_transactions:
                transactions = direct_transactions
            latest = direct_latest or latest
            actions.append(
                RestartSyncAction(
                    action="broker_transactions_sinceid_loaded",
                    severity="info",
                    message="broker transaction history loaded since previous checkpoint",
                    details={"previous_transaction_id": previous, "transactions": len(direct_transactions)},
                )
            )
        except Exception as exc:
            actions.append(
                RestartSyncAction(
                    action="broker_transaction_history_failed",
                    severity="blocking" if not transactions else "warning",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__, "source": "transactions_sinceid"},
                )
            )

    if not previous:
        try:
            if hasattr(broker, "get_latest_transaction_id"):
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
                    details={"exception_type": type(exc).__name__, "source": "latest_transaction_id"},
                )
            )
    elif not hasattr(broker, "get_account_changes") and not hasattr(broker, "get_transactions_since"):
        try:
            if hasattr(broker, "get_latest_transaction_id"):
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
                    details={"exception_type": type(exc).__name__, "source": "latest_transaction_id"},
                )
            )
    elif not latest and hasattr(broker, "get_latest_transaction_id"):
        try:
            latest = getattr(broker, "get_latest_transaction_id")()
            actions.append(
                RestartSyncAction(
                    action="broker_transaction_checkpoint_loaded",
                    severity="info",
                    message="latest broker transaction checkpoint loaded",
                    details={"latest_transaction_id": latest},
                )
            )
        except Exception as exc:
            actions.append(
                RestartSyncAction(
                    action="broker_transaction_sync_failed",
                    severity="blocking",
                    message=str(exc),
                    details={"exception_type": type(exc).__name__, "source": "latest_transaction_id"},
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


def _extract_transactions(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    direct = payload.get("transactions")
    if isinstance(direct, list):
        records.extend(dict(item) for item in direct if isinstance(item, dict))
    changes = payload.get("changes")
    if isinstance(changes, dict):
        nested = changes.get("transactions")
        if isinstance(nested, list):
            records.extend(dict(item) for item in nested if isinstance(item, dict))
    return _dedupe_transactions(records)


def _dedupe_transactions(records: list[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    by_key: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        transaction_id = _optional_str(record.get("id") or record.get("transactionID"))
        by_key[transaction_id or f"row:{index}"] = record
    return tuple(sorted(by_key.values(), key=_transaction_sort_key))


def _transaction_sort_key(transaction: dict[str, Any]) -> tuple[pd.Timestamp, int]:
    timestamp = pd.to_datetime(transaction.get("time"), errors="coerce", utc=True)
    if timestamp is pd.NaT:
        timestamp = pd.Timestamp.min.tz_localize("UTC")
    try:
        transaction_id = int(str(transaction.get("id", "0")))
    except (TypeError, ValueError):
        transaction_id = 0
    return timestamp, transaction_id


def _transaction_context(transactions: tuple[dict[str, Any], ...]) -> dict[str, dict[str, dict[str, Any]]]:
    context: dict[str, dict[str, dict[str, Any]]] = {"opened": {}, "closed": {}, "reduced": {}}
    for transaction in transactions:
        item = _transaction_context_item(transaction)
        for position_id in _trade_opened_ids(transaction):
            context["opened"][position_id] = item
        for position_id in _trade_closed_ids(transaction):
            context["closed"][position_id] = item
        for position_id in _trade_reduced_ids(transaction):
            context["reduced"][position_id] = item
    return context


def _transaction_context_item(transaction: dict[str, Any]) -> dict[str, Any]:
    return {
        "transaction_id": _transaction_id(transaction),
        "transaction_type": _transaction_type(transaction),
        "timestamp": _optional_datetime(transaction.get("time")),
        "price": _optional_float(transaction.get("price")),
        "pl": _optional_float(transaction.get("pl")),
        "financing": _optional_float(transaction.get("financing")),
        "commission": _optional_float(transaction.get("commission")),
        "account_balance": _optional_float(transaction.get("accountBalance")),
        "reason": _optional_str(transaction.get("reason") or transaction.get("rejectReason")),
        "order_id": _optional_str(transaction.get("orderID")),
        "client_order_id": _optional_str(transaction.get("clientOrderID")),
    }


def _reconcile_transactions(
    transactions: tuple[dict[str, Any], ...],
    *,
    broker_positions: tuple[Position, ...],
    expected_positions: tuple[Position, ...],
    config: RestartSyncConfig,
    symbol: str | None,
) -> tuple[TransactionReconciliationEvent, ...]:
    broker_by_id = _position_alias_map(broker_positions)
    expected_by_id = _position_alias_map(expected_positions)
    symbol_filter = symbol.upper() if symbol else None
    events: list[TransactionReconciliationEvent] = []
    for transaction in transactions:
        transaction_symbol = _transaction_symbol(transaction)
        if symbol_filter and transaction_symbol and transaction_symbol != symbol_filter:
            continue
        transaction_type = _transaction_type(transaction)
        upper_type = transaction_type.upper()
        if "REJECT" in upper_type:
            events.append(_rejected_transaction_event(transaction, config))
            continue
        if "CANCEL" in upper_type:
            events.append(_cancelled_transaction_event(transaction, config))
            continue
        if upper_type == "ORDER_FILL":
            events.extend(_order_fill_events(transaction, broker_by_id, expected_by_id))
            continue
        if "FINANCING" in upper_type:
            events.append(_account_transaction_event("oanda_financing_transaction", transaction, "financing transaction observed"))
            continue
        if "TRANSFER" in upper_type or "FUND" in upper_type:
            events.append(_account_transaction_event("oanda_funding_transaction", transaction, "funding transaction observed"))
            continue
        if "MARGIN" in upper_type:
            events.append(_account_transaction_event("oanda_margin_transaction", transaction, "margin transaction observed"))
    return tuple(events)


def _transaction_event_actions(events: tuple[TransactionReconciliationEvent, ...]) -> list[RestartSyncAction]:
    if not events:
        return []
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event] = counts.get(event.event, 0) + 1
    return [
        RestartSyncAction(
            action="broker_transactions_reconciled",
            severity="info",
            message="broker transactions classified and compared with broker/ledger state",
            details={
                "events": len(events),
                "blocking_events": sum(1 for event in events if event.blocking),
                "event_counts": counts,
            },
        )
    ]


def _order_fill_events(
    transaction: dict[str, Any],
    broker_by_id: dict[str, Position],
    expected_by_id: dict[str, Position],
) -> list[TransactionReconciliationEvent]:
    events: list[TransactionReconciliationEvent] = []
    opened_ids = _trade_opened_ids(transaction)
    closed_ids = _trade_closed_ids(transaction)
    reduced_ids = _trade_reduced_ids(transaction)
    for position_id in opened_ids:
        if position_id in broker_by_id and position_id in expected_by_id:
            events.append(
                _position_transaction_event(
                    "oanda_trade_open_confirmed",
                    transaction,
                    position_id,
                    "info",
                    "OANDA opened trade is already present in the expected ledger",
                )
            )
        elif position_id in broker_by_id:
            events.append(
                _position_transaction_event(
                    "oanda_trade_open_missing_from_ledger",
                    transaction,
                    position_id,
                    "warning",
                    "OANDA opened trade is broker-open but missing from the expected ledger",
                )
            )
        else:
            events.append(
                _position_transaction_event(
                    "oanda_trade_open_not_currently_open",
                    transaction,
                    position_id,
                    "info",
                    "OANDA opened trade is no longer open at the broker",
                )
            )
    for position_id in closed_ids:
        if position_id in broker_by_id:
            events.append(
                _position_transaction_event(
                    "oanda_trade_close_broker_still_open",
                    transaction,
                    position_id,
                    "blocking",
                    "OANDA close transaction was observed but broker still reports the trade open",
                )
            )
        elif position_id in expected_by_id:
            events.append(
                _position_transaction_event(
                    "oanda_trade_closed",
                    transaction,
                    position_id,
                    "warning",
                    "OANDA closed an expected trade while the bot was offline",
                )
            )
        else:
            events.append(
                _position_transaction_event(
                    "oanda_unknown_trade_closed",
                    transaction,
                    position_id,
                    "info",
                    "OANDA closed a trade that is not open in the current broker or ledger snapshot",
                )
            )
    for position_id in reduced_ids:
        if position_id in broker_by_id and position_id in expected_by_id:
            events.append(
                _position_transaction_event(
                    "oanda_trade_reduced",
                    transaction,
                    position_id,
                    "warning",
                    "OANDA reduced a broker-open expected trade while the bot was offline",
                )
            )
        elif position_id in expected_by_id:
            events.append(
                _position_transaction_event(
                    "oanda_trade_reduced_not_broker_open",
                    transaction,
                    position_id,
                    "warning",
                    "OANDA reduced an expected trade that is not present in the broker-open snapshot",
                )
            )
        else:
            events.append(
                _position_transaction_event(
                    "oanda_unknown_trade_reduced",
                    transaction,
                    position_id,
                    "info",
                    "OANDA reduced a trade that is not open in the current broker or ledger snapshot",
                )
            )
    if not opened_ids and not closed_ids and not reduced_ids:
        events.append(_account_transaction_event("oanda_order_fill_seen", transaction, "OANDA order fill observed"))
    return events


def _rejected_transaction_event(
    transaction: dict[str, Any],
    config: RestartSyncConfig,
) -> TransactionReconciliationEvent:
    severity = "blocking" if config.block_on_rejected_transactions else "warning"
    reason = _optional_str(transaction.get("rejectReason") or transaction.get("reason"))
    message = "OANDA rejected an order transaction"
    if reason:
        message = f"{message}: {reason}"
    return _base_transaction_event(
        "oanda_order_rejected",
        transaction,
        severity,
        message,
    )


def _cancelled_transaction_event(
    transaction: dict[str, Any],
    config: RestartSyncConfig,
) -> TransactionReconciliationEvent:
    severity = "blocking" if config.block_on_cancelled_transactions else "warning"
    reason = _optional_str(transaction.get("reason") or transaction.get("cancellingTransactionID"))
    message = "OANDA cancelled an order transaction"
    if reason:
        message = f"{message}: {reason}"
    return _base_transaction_event(
        "oanda_order_cancelled",
        transaction,
        severity,
        message,
    )


def _account_transaction_event(event: str, transaction: dict[str, Any], message: str) -> TransactionReconciliationEvent:
    return _base_transaction_event(event, transaction, "info", message)


def _position_transaction_event(
    event: str,
    transaction: dict[str, Any],
    position_id: str,
    severity: str,
    message: str,
) -> TransactionReconciliationEvent:
    return _base_transaction_event(event, transaction, severity, message, position_id=position_id)


def _base_transaction_event(
    event: str,
    transaction: dict[str, Any],
    severity: str,
    message: str,
    *,
    position_id: str | None = None,
) -> TransactionReconciliationEvent:
    return TransactionReconciliationEvent(
        event=event,
        severity=severity,
        message=message,
        transaction_id=_transaction_id(transaction),
        transaction_type=_transaction_type(transaction),
        symbol=_transaction_symbol(transaction),
        position_id=position_id or _first_trade_id(transaction),
        order_id=_optional_str(transaction.get("orderID")),
        client_order_id=_optional_str(transaction.get("clientOrderID")),
        timestamp=_optional_datetime(transaction.get("time")),
        details={
            "reason": _optional_str(transaction.get("reason") or transaction.get("rejectReason")),
            "price": _optional_float(transaction.get("price")),
            "units": _optional_float(transaction.get("units")),
            "pl": _optional_float(transaction.get("pl")),
            "financing": _optional_float(transaction.get("financing")),
            "commission": _optional_float(transaction.get("commission")),
            "account_balance": _optional_float(transaction.get("accountBalance")),
        },
    )


def _position_alias_map(positions: tuple[Position, ...]) -> dict[str, Position]:
    aliases: dict[str, Position] = {}
    for position in positions:
        aliases[position.position_id] = position
        raw_trade_ids = position.metadata.get("oanda_trade_ids", ())
        if isinstance(raw_trade_ids, str):
            aliases[raw_trade_ids] = position
        else:
            for item in raw_trade_ids:
                aliases[str(item)] = position
    return aliases


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
    transaction_context: dict[str, dict[str, dict[str, Any]]],
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
        transaction = transaction_context["opened"].get(position_id)
        synced = _synced_position(position, action="adopted_after_restart", transaction=transaction)
        ledger.record_open_position(synced)
        details = {"units": synced.units, "side": synced.side, "entry_price": synced.entry_price}
        details.update(_transaction_action_details(transaction))
        actions.append(
            RestartSyncAction(
                action="adopt_broker_position",
                severity="info",
                message="broker position adopted into expected ledger after restart",
                symbol=synced.symbol,
                position_id=synced.position_id,
                details=details,
            )
        )


def _mark_missing_expected_positions(
    result: ReconciliationResult,
    expected_by_id: dict[str, Position],
    ledger: PositionLedger,
    config: RestartSyncConfig,
    actions: list[RestartSyncAction],
    transaction_context: dict[str, dict[str, dict[str, Any]]],
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
        transaction = transaction_context["closed"].get(position_id)
        close_price = _optional_float(transaction.get("price")) if transaction else None
        closed_at = transaction.get("timestamp") if transaction else utc_now()
        ledger.record_closed_position(position_id, exit_price=close_price, closed_at=closed_at)
        details = {"side": expected.side, "units": expected.units, "entry_price": expected.entry_price}
        details.update(_transaction_action_details(transaction))
        actions.append(
            RestartSyncAction(
                action="mark_expected_position_closed",
                severity="warning",
                message="expected ledger position was not open at broker and was marked closed",
                symbol=expected.symbol,
                position_id=position_id,
                details=details,
            )
        )


def _update_mismatched_positions(
    result: ReconciliationResult,
    broker_by_id: dict[str, Position],
    ledger: PositionLedger,
    config: RestartSyncConfig,
    actions: list[RestartSyncAction],
    transaction_context: dict[str, dict[str, dict[str, Any]]],
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
        transaction = transaction_context["reduced"].get(position_id) or transaction_context["opened"].get(position_id)
        position = _synced_position(broker_by_id[position_id], action="updated_after_restart", transaction=transaction)
        ledger.record_open_position(position)
        details = {"side": position.side, "units": position.units, "entry_price": position.entry_price}
        details.update(_transaction_action_details(transaction))
        actions.append(
            RestartSyncAction(
                action="update_expected_position_from_broker",
                severity="warning",
                message="expected ledger position was updated from broker state after restart",
                symbol=position.symbol,
                position_id=position.position_id,
                details=details,
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


def _synced_position(
    position: Position,
    *,
    action: str,
    transaction: dict[str, Any] | None = None,
) -> Position:
    synced = deepcopy(position)
    metadata = dict(synced.metadata)
    restart_sync = {
        "action": action,
        "synced_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    restart_sync.update(_transaction_action_details(transaction))
    metadata["restart_sync"] = restart_sync
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


def _transaction_action_details(transaction: dict[str, Any] | None) -> dict[str, Any]:
    if not transaction:
        return {}
    details = {
        "oanda_transaction_id": _optional_str(transaction.get("transaction_id")),
        "oanda_transaction_type": _optional_str(transaction.get("transaction_type")),
        "oanda_transaction_time": _jsonable(transaction.get("timestamp")),
        "oanda_order_id": _optional_str(transaction.get("order_id")),
        "oanda_client_order_id": _optional_str(transaction.get("client_order_id")),
        "oanda_reason": _optional_str(transaction.get("reason")),
        "exit_price": _optional_float(transaction.get("price")),
        "realized_pl": _optional_float(transaction.get("pl")),
        "financing": _optional_float(transaction.get("financing")),
        "commission": _optional_float(transaction.get("commission")),
        "account_balance": _optional_float(transaction.get("account_balance")),
    }
    return {key: value for key, value in details.items() if value is not None}


def _transaction_id(transaction: dict[str, Any]) -> str | None:
    return _optional_str(transaction.get("id") or transaction.get("transactionID") or transaction.get("transaction_id"))


def _transaction_type(transaction: dict[str, Any]) -> str:
    return _optional_str(transaction.get("type") or transaction.get("transactionType")) or ""


def _transaction_symbol(transaction: dict[str, Any]) -> str | None:
    instrument = _optional_str(transaction.get("instrument"))
    return instrument.replace("_", "").upper() if instrument else None


def _first_trade_id(transaction: dict[str, Any]) -> str | None:
    for values in (_trade_opened_ids(transaction), _trade_reduced_ids(transaction), _trade_closed_ids(transaction)):
        if values:
            return values[0]
    return _optional_str(transaction.get("tradeID") or transaction.get("tradeId") or transaction.get("trade_id"))


def _trade_opened_ids(transaction: dict[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    _append_optional(ids, transaction.get("tradeOpenedID"))
    _append_trade_id_from_mapping(ids, transaction.get("tradeOpened"))
    _append_trade_ids_from_list(ids, transaction.get("tradesOpened"))
    return tuple(dict.fromkeys(ids))


def _trade_closed_ids(transaction: dict[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    raw_ids = transaction.get("tradeClosedIDs")
    if isinstance(raw_ids, list):
        for item in raw_ids:
            _append_optional(ids, item)
    _append_trade_id_from_mapping(ids, transaction.get("tradeClosed"))
    _append_trade_ids_from_list(ids, transaction.get("tradesClosed"))
    return tuple(dict.fromkeys(ids))


def _trade_reduced_ids(transaction: dict[str, Any]) -> tuple[str, ...]:
    ids: list[str] = []
    _append_optional(ids, transaction.get("tradeReducedID"))
    _append_trade_id_from_mapping(ids, transaction.get("tradeReduced"))
    _append_trade_ids_from_list(ids, transaction.get("tradesReduced"))
    return tuple(dict.fromkeys(ids))


def _append_trade_ids_from_list(ids: list[str], value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        _append_trade_id_from_mapping(ids, item)


def _append_trade_id_from_mapping(ids: list[str], value: object) -> None:
    if not isinstance(value, dict):
        return
    _append_optional(ids, value.get("tradeID") or value.get("tradeId") or value.get("id"))


def _append_optional(values: list[str], value: object) -> None:
    text = _optional_str(value)
    if text:
        values.append(text)


def _optional_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    timestamp = pd.to_datetime(value, errors="coerce", utc=True)
    if timestamp is pd.NaT:
        return None
    return timestamp.to_pydatetime()


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
        "transaction_events": [_jsonable(asdict(event)) for event in report.transaction_events],
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
