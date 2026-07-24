from __future__ import annotations

import pandas as pd

from smc_ta.broker import BrokerOrder, OrderRequest, PaperBroker, Position
from smc_ta.live import DemoTradingBot
from smc_ta.reconciliation import (
    BrokerReconciler,
    MemoryPositionLedger,
    MemorySyncCheckpointStore,
    RestartSyncConfig,
    SQLiteSyncCheckpointStore,
    sync_broker_state_after_restart,
)
from smc_ta.risk import RiskConfig, RiskManager


def position(position_id: str = "p1", *, units: float = 10_000, entry: float = 1.1000) -> Position:
    return Position(
        position_id=position_id,
        symbol="EURUSD",
        side="long",
        units=units,
        entry_price=entry,
        opened_at=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )


def test_restart_sync_report_only_blocks_without_mutating_ledger() -> None:
    broker = PaperBroker(initial_balance=10_000)
    broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
    )
    ledger = MemoryPositionLedger([position("expected_only")])

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        config=RestartSyncConfig(fetch_broker_transactions=False, fetch_pending_orders=False),
    )

    assert not report.ok
    assert "unmanaged_broker_position" in report.blocking_reasons
    assert "missing_broker_position" in report.blocking_reasons
    assert [item.position_id for item in ledger.open_positions("EURUSD")] == ["expected_only"]


def test_restart_sync_can_adopt_broker_positions_and_close_stale_expected_rows() -> None:
    broker = PaperBroker(initial_balance=10_000)
    fill = broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
    )
    ledger = MemoryPositionLedger([position("expected_only")])

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        config=RestartSyncConfig(
            adopt_unmanaged_broker_positions=True,
            mark_missing_expected_positions_closed=True,
            fetch_broker_transactions=False,
            fetch_pending_orders=False,
        ),
    )

    assert report.ok
    assert report.summary() == "restart_sync_ok"
    assert {action.action for action in report.actions} == {
        "adopt_broker_position",
        "mark_expected_position_closed",
    }
    open_ids = [item.position_id for item in ledger.open_positions("EURUSD")]
    assert open_ids == [fill.order_id]
    assert ledger.open_positions("EURUSD")[0].metadata["restart_sync"]["action"] == "adopted_after_restart"


def test_restart_sync_can_update_mismatched_expected_position() -> None:
    broker = PaperBroker(initial_balance=10_000)
    fill = broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=2_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
    )
    ledger = MemoryPositionLedger([position(fill.order_id, units=1_000, entry=1.2000)])

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        config=RestartSyncConfig(
            update_mismatched_expected_positions=True,
            fetch_broker_transactions=False,
            fetch_pending_orders=False,
        ),
    )

    assert report.ok
    synced = ledger.open_positions("EURUSD")[0]
    assert synced.units == 2_000
    assert synced.metadata["restart_sync"]["action"] == "updated_after_restart"
    assert "update_expected_position_from_broker" in {action.action for action in report.actions}


class TransactionSyncBroker:
    def __init__(self) -> None:
        self.positions = [position("broker_trade_1")]
        self.pending_orders = [
            BrokerOrder(
                order_id="tp_1",
                symbol=None,
                order_type="TAKE_PROFIT",
                state="PENDING",
                price=1.1200,
                trade_id="broker_trade_1",
            ),
            BrokerOrder(
                order_id="entry_1",
                symbol="EURUSD",
                order_type="LIMIT",
                state="PENDING",
                side="buy",
                units=1_000,
                price=1.0950,
            ),
        ]

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        symbol_filter = symbol.upper() if symbol else None
        return [item for item in self.positions if symbol_filter is None or item.symbol == symbol_filter]

    def get_account_changes(self, since_transaction_id: str) -> dict:
        assert since_transaction_id == "99"
        return {
            "lastTransactionID": "101",
            "changes": {
                "transactions": [
                    {"id": "100", "type": "MARKET_ORDER", "instrument": "EUR_USD"},
                    {"id": "101", "type": "ORDER_FILL", "instrument": "EUR_USD"},
                ]
            },
        }

    def get_pending_orders(self, symbol: str | None = None) -> list[BrokerOrder]:
        return list(self.pending_orders)


def test_restart_sync_persists_transaction_checkpoint_and_blocks_unlinked_pending_orders() -> None:
    broker = TransactionSyncBroker()
    ledger = MemoryPositionLedger([position("broker_trade_1")])
    checkpoints = MemorySyncCheckpointStore({"broker_transaction_id": "99"})

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        checkpoint_store=checkpoints,
    )

    assert not report.ok
    assert checkpoints.get_checkpoint("broker_transaction_id") == "101"
    assert report.previous_transaction_id == "99"
    assert report.latest_transaction_id == "101"
    assert len(report.transactions) == 2
    assert len(report.pending_orders) == 2
    assert "pending_order_linked_to_position" in {action.action for action in report.actions}
    assert "unlinked_pending_order" in report.blocking_reasons
    assert not report.to_frame().empty
    assert not report.orders_frame().empty
    assert not report.transactions_frame().empty


def test_sqlite_checkpoint_store_persists_values(tmp_path) -> None:
    path = tmp_path / "restart_sync.sqlite"
    store = SQLiteSyncCheckpointStore(path)
    store.set_checkpoint("oanda_account", "123")

    reopened = SQLiteSyncCheckpointStore(path)
    assert reopened.get_checkpoint("oanda_account") == "123"


def test_demo_bot_exposes_restart_sync_helper() -> None:
    broker = PaperBroker(initial_balance=10_000)
    fill = broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-02", tz="UTC").to_pydatetime(),
    )
    ledger = MemoryPositionLedger([position(fill.order_id, units=1_000, entry=1.10007)])
    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=broker,
        risk_manager=RiskManager(RiskConfig(min_confidence=0.0)),
        reconciler=BrokerReconciler(ledger),
    )

    report = bot.sync_after_restart(
        config=RestartSyncConfig(fetch_broker_transactions=False, fetch_pending_orders=False)
    )

    assert report.ok
    assert report.summary() == "restart_sync_ok"
