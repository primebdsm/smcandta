from __future__ import annotations

from dataclasses import replace

import pandas as pd

from smc_ta.broker import OrderRequest, PaperBroker, Position
from smc_ta.lifecycle import (
    LifecycleRecoveryConfig,
    MemoryTradeLifecycleStore,
    TradeLifecycleStateMachine,
    recover_lifecycle_after_restart,
    write_lifecycle_recovery_report,
)
from smc_ta.live import DemoTradingBot
from smc_ta.risk import RiskConfig, RiskManager


def position(position_id: str = "broker-p1", *, units: float = 1_000, side: str = "long") -> Position:
    return Position(
        position_id=position_id,
        symbol="EURUSD",
        side=side,  # type: ignore[arg-type]
        units=units,
        entry_price=1.1000,
        opened_at=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
        stop_loss=1.0950 if side == "long" else 1.1050,
        take_profit=1.1100 if side == "long" else 1.0900,
    )


def signal(side: str = "long") -> pd.Series:
    return pd.Series(
        {
            "side": side,
            "confidence": 0.8,
            "entry_reference": 1.1000,
            "stop_reference": 1.0950 if side == "long" else 1.1050,
            "target_reference": 1.1100 if side == "long" else 1.0900,
            "reference_rr": 2.0,
            "long_score": 1.0 if side == "long" else 0.0,
            "short_score": 1.0 if side == "short" else 0.0,
            "reasons": "test",
        }
    )


def open_lifecycle(position_id: str = "broker-p1", *, units: float = 1_000):
    machine = TradeLifecycleStateMachine()
    record = machine.create_from_signal(
        symbol="EURUSD",
        timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
        signal=signal("long"),
        setup_name="test_setup",
    )
    order = OrderRequest(symbol="EURUSD", side="buy", units=units, stop_loss=1.0950, take_profit=1.1100)
    record = machine.approve(record, order=order)
    record = machine.submit(record, order)
    fill = PaperBroker(initial_balance=10_000).place_order(
        replace(order, client_order_id=order.client_order_id),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )
    fill = replace(fill, order_id=position_id, units=units, price=1.1000)
    return machine.record_fill(record, fill, position_id=position_id)


def submitted_lifecycle(position_id: str = "broker-p1"):
    machine = TradeLifecycleStateMachine()
    record = machine.create_from_signal(
        symbol="EURUSD",
        timestamp=pd.Timestamp("2024-01-01", tz="UTC"),
        signal=signal("long"),
        setup_name="test_setup",
    )
    order = OrderRequest(symbol="EURUSD", side="buy", units=1_000, stop_loss=1.0950, take_profit=1.1100)
    record = machine.approve(record, order=order)
    record = machine.submit(record, order)
    return replace(record, position_id=position_id)


def broker_with(position_item: Position | None) -> PaperBroker:
    broker = PaperBroker(initial_balance=10_000)
    if position_item is not None:
        broker.positions[position_item.position_id] = position_item
    return broker


def actions(report) -> set[str]:
    return {action.action for action in report.actions}


def test_lifecycle_recovery_syncs_matched_open_record_from_broker() -> None:
    store = MemoryTradeLifecycleStore()
    store.save(open_lifecycle("broker-p1", units=1_000))
    broker = broker_with(position("broker-p1", units=2_000))

    report = recover_lifecycle_after_restart(broker, store, symbol="EURUSD")

    recovered = store.list_records(symbol="EURUSD")[0]
    assert report.ok
    assert "sync_lifecycle_from_broker_position" in actions(report)
    assert recovered.units == 2_000
    assert recovered.filled_units == 2_000
    assert recovered.metadata["lifecycle_recovery"]["action"] == "lifecycle_synced_with_broker_position_after_restart"


def test_lifecycle_recovery_creates_lifecycle_for_untracked_broker_position() -> None:
    store = MemoryTradeLifecycleStore()
    broker = broker_with(position("broker-only"))

    blocked = recover_lifecycle_after_restart(broker, store, symbol="EURUSD")
    assert not blocked.ok
    assert "untracked_broker_position_lifecycle" in blocked.blocking_reasons

    recovered = recover_lifecycle_after_restart(
        broker,
        store,
        symbol="EURUSD",
        config=LifecycleRecoveryConfig(create_missing_lifecycles_for_broker_positions=True),
    )

    records = store.list_records(symbol="EURUSD")
    assert recovered.ok
    assert "create_lifecycle_from_broker_position" in actions(recovered)
    assert len(records) == 1
    assert records[0].state == "open"
    assert records[0].position_id == "broker-only"
    assert records[0].setup_name == "broker_recovered_after_restart"


def test_lifecycle_recovery_marks_missing_open_lifecycle_closed() -> None:
    store = MemoryTradeLifecycleStore()
    store.save(open_lifecycle("missing-position"))
    broker = broker_with(None)

    blocked = recover_lifecycle_after_restart(broker, store, symbol="EURUSD")
    assert not blocked.ok
    assert "lifecycle_missing_broker_position" in blocked.blocking_reasons

    recovered = recover_lifecycle_after_restart(
        broker,
        store,
        symbol="EURUSD",
        config=LifecycleRecoveryConfig(mark_missing_broker_positions_closed=True),
    )

    record = store.list_records(symbol="EURUSD")[0]
    assert recovered.ok
    assert "mark_lifecycle_closed_missing_broker_position" in actions(recovered)
    assert record.state == "closed"


def test_lifecycle_recovery_opens_submitted_record_from_broker_position() -> None:
    store = MemoryTradeLifecycleStore()
    store.save(submitted_lifecycle("broker-p1"))
    broker = broker_with(position("broker-p1", units=1_000))

    report = recover_lifecycle_after_restart(broker, store, symbol="EURUSD")

    record = store.list_records(symbol="EURUSD")[0]
    assert report.ok
    assert "open_lifecycle_from_broker_position" in actions(report)
    assert record.state == "open"
    assert record.position_id == "broker-p1"


def test_lifecycle_recovery_blocks_duplicate_lifecycle_position() -> None:
    store = MemoryTradeLifecycleStore()
    store.save(open_lifecycle("broker-p1"))
    store.save(replace(open_lifecycle("broker-p1"), trade_id="duplicate-record"))
    broker = broker_with(position("broker-p1"))

    report = recover_lifecycle_after_restart(broker, store, symbol="EURUSD")

    assert not report.ok
    assert "duplicate_lifecycle_position" in report.blocking_reasons


def test_lifecycle_recovery_bot_helper_and_json_report(tmp_path) -> None:
    store = MemoryTradeLifecycleStore()
    broker = broker_with(position("broker-p1"))
    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=broker,
        risk_manager=RiskManager(RiskConfig(min_confidence=0.0)),
        trade_lifecycle_store=store,
    )

    report = bot.recover_lifecycle_after_restart(
        config=LifecycleRecoveryConfig(create_missing_lifecycles_for_broker_positions=True)
    )
    output = write_lifecycle_recovery_report(report, tmp_path / "lifecycle_recovery.json")

    assert report.ok
    assert output.exists()
    assert "lifecycle_recovery_ok" in output.read_text()
