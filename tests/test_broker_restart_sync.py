from __future__ import annotations

import pandas as pd

from smc_ta.broker import BrokerOrder, OandaBroker, OandaConfig, OrderRequest, PaperBroker, Position
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


class OandaTransactionCloseBroker:
    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        return []

    def get_account_changes(self, since_transaction_id: str) -> dict:
        assert since_transaction_id == "120"
        return {
            "lastTransactionID": "121",
            "changes": {
                "transactions": [
                    {
                        "id": "121",
                        "time": "2024-01-02T12:00:00Z",
                        "type": "ORDER_FILL",
                        "instrument": "EUR_USD",
                        "orderID": "close_order_1",
                        "price": "1.09000",
                        "pl": "-100.0",
                        "commission": "-0.5",
                        "tradesClosed": [{"tradeID": "broker_trade_1", "units": "1000", "realizedPL": "-100.0"}],
                    }
                ]
            },
        }


def test_restart_sync_uses_oanda_close_transaction_to_close_expected_position() -> None:
    broker = OandaTransactionCloseBroker()
    ledger = MemoryPositionLedger([position("broker_trade_1")])
    checkpoints = MemorySyncCheckpointStore({"broker_transaction_id": "120"})

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        checkpoint_store=checkpoints,
        config=RestartSyncConfig(
            mark_missing_expected_positions_closed=True,
            fetch_pending_orders=False,
        ),
    )

    assert report.ok
    assert checkpoints.get_checkpoint("broker_transaction_id") == "121"
    assert ledger.open_positions("EURUSD") == []
    assert ledger.positions["broker_trade_1"].exit_price == 1.09
    assert ledger.positions["broker_trade_1"].closed_at == pd.Timestamp("2024-01-02T12:00:00Z").to_pydatetime()
    assert ledger.positions["broker_trade_1"].metadata == {}
    assert report.transaction_events[0].event == "oanda_trade_closed"
    assert report.transaction_events[0].position_id == "broker_trade_1"
    assert "mark_expected_position_closed" in {action.action for action in report.actions}
    close_action = [action for action in report.actions if action.action == "mark_expected_position_closed"][0]
    assert close_action.details["oanda_transaction_id"] == "121"
    assert close_action.details["exit_price"] == 1.09


class OandaRejectedTransactionBroker:
    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        return [position("broker_trade_1")]

    def get_account_changes(self, since_transaction_id: str) -> dict:
        return {
            "lastTransactionID": "131",
            "changes": {
                "transactions": [
                    {
                        "id": "131",
                        "time": "2024-01-02T12:01:00Z",
                        "type": "MARKET_ORDER_REJECT",
                        "instrument": "EUR_USD",
                        "rejectReason": "STOP_LOSS_ON_FILL_PRICE_DISTANCE_MAXIMUM_EXCEEDED",
                    }
                ]
            },
        }


def test_restart_sync_blocks_oanda_rejected_transactions_by_default() -> None:
    broker = OandaRejectedTransactionBroker()
    ledger = MemoryPositionLedger([position("broker_trade_1")])

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        checkpoint_store=MemorySyncCheckpointStore({"broker_transaction_id": "130"}),
        config=RestartSyncConfig(fetch_pending_orders=False),
    )

    assert not report.ok
    assert "oanda_order_rejected" in report.blocking_reasons
    assert report.transaction_events[0].event == "oanda_order_rejected"
    assert report.transaction_events[0].severity == "blocking"
    assert "STOP_LOSS_ON_FILL" in report.transaction_events[0].message


def test_restart_sync_can_warn_for_oanda_rejected_transactions() -> None:
    broker = OandaRejectedTransactionBroker()
    ledger = MemoryPositionLedger([position("broker_trade_1")])

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol="EURUSD",
        checkpoint_store=MemorySyncCheckpointStore({"broker_transaction_id": "130"}),
        config=RestartSyncConfig(
            block_on_rejected_transactions=False,
            fetch_pending_orders=False,
        ),
    )

    assert report.ok
    assert report.transaction_events[0].severity == "warning"


class FakeOandaClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, *, params=None, payload=None) -> dict:
        self.calls.append((method, path, params))
        return {"lastTransactionID": "201", "transactions": [{"id": "201", "type": "DAILY_FINANCING"}]}


def test_oanda_broker_exposes_transactions_sinceid_endpoint() -> None:
    broker = OandaBroker(OandaConfig(account_id="acct", token="token"))
    fake_client = FakeOandaClient()
    broker.client = fake_client  # type: ignore[assignment]

    response = broker.get_transactions_since("200", transaction_types=("ORDER_FILL",))

    assert response["lastTransactionID"] == "201"
    assert fake_client.calls == [
        (
            "GET",
            "/accounts/acct/transactions/sinceid",
            {"id": "200", "type": "ORDER_FILL"},
        )
    ]


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
