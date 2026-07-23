from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from smc_ta import ConfluenceConfig, DemoTradingBot
from smc_ta.broker import OrderFill, OrderRequest, PaperBroker
from smc_ta.lifecycle import (
    MemoryTradeLifecycleStore,
    SQLiteTradeLifecycleStore,
    TradeLifecycleError,
    TradeLifecycleStateMachine,
)
from smc_ta.risk import RiskConfig, RiskManager


def make_signal(side: str = "long") -> pd.Series:
    return pd.Series(
        {
            "side": side,
            "confidence": 0.75,
            "entry_reference": 1.1000,
            "stop_reference": 1.0950,
            "target_reference": 1.1100,
            "reference_rr": 2.0,
            "long_score": 8,
            "short_score": 2,
            "reasons": "discount_zone;near_bullish_order_block",
        }
    )


def make_fill(order: OrderRequest, *, order_id: str = "broker-1") -> OrderFill:
    return OrderFill(
        order_id=order_id,
        symbol=order.symbol,
        side=order.side,
        units=order.units,
        price=1.1001,
        spread=0.0001,
        slippage=0.00002,
        commission=0.0,
        timestamp=datetime.now(timezone.utc),
        client_order_id=order.client_order_id,
    )


def make_candles(n: int = 90) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + np.sin(np.arange(n) / 5) * 0.0007, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0003
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0003
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 20),
            "spread": 0.0001,
        },
        index=index,
    )


def test_trade_lifecycle_signal_to_open_to_close() -> None:
    machine = TradeLifecycleStateMachine()
    signal = make_signal()
    order = OrderRequest(symbol="EURUSD", side="buy", units=20_000, stop_loss=1.0950, take_profit=1.1100)

    record = machine.create_from_signal(
        symbol="EURUSD",
        timestamp=pd.Timestamp("2024-01-01T12:00:00Z"),
        signal=signal,
        setup_name="order_block_mitigation",
    )
    record = machine.approve(record, order=order)
    record = machine.submit(record, order)
    record = machine.record_fill(record, make_fill(order), position_id="pos-1")
    record = machine.record_partial_close(record, price=1.1050, units=10_000, pnl=49.0)
    record = machine.record_close(record, price=1.1080, units=10_000, pnl=79.0)

    assert record.state == "closed"
    assert record.symbol == "EURUSD"
    assert record.position_id == "pos-1"
    assert record.closed_units == 20_000
    assert record.realized_pnl == 128.0
    assert [event.event_type for event in record.history] == [
        "signal",
        "approved",
        "submitted",
        "filled",
        "partial_close",
        "closed",
    ]
    assert machine.to_frame(record)["state_to"].iloc[-1] == "closed"


def test_trade_lifecycle_blocks_invalid_terminal_transition() -> None:
    machine = TradeLifecycleStateMachine()
    record = machine.create_from_signal(
        symbol="EURUSD",
        timestamp=pd.Timestamp("2024-01-01T12:00:00Z"),
        signal=make_signal("flat"),
    )
    blocked = machine.block(record, "signal_is_flat", source="risk")
    order = OrderRequest(symbol="EURUSD", side="buy", units=10_000)

    assert blocked.is_terminal
    with pytest.raises(TradeLifecycleError):
        machine.approve(blocked, order=order)


def test_sqlite_trade_lifecycle_store_roundtrip_and_filters(tmp_path) -> None:
    machine = TradeLifecycleStateMachine()
    store = SQLiteTradeLifecycleStore(tmp_path / "lifecycles.sqlite")
    signal = make_signal()
    eurusd = machine.create_from_signal(symbol="EURUSD", timestamp=pd.Timestamp("2024-01-01T12:00:00Z"), signal=signal)
    eurusd = machine.block(eurusd, "news_filter_blocked", source="news")
    gbpusd = machine.create_from_signal(symbol="GBPUSD", timestamp=pd.Timestamp("2024-01-01T12:15:00Z"), signal=signal)

    store.save(eurusd)
    store.save(gbpusd)

    loaded = store.get(eurusd.trade_id)
    assert loaded is not None
    assert loaded.state == "blocked"
    assert loaded.history[-1].metadata["source"] == "news"
    assert [record.symbol for record in store.list_records(symbol="EURUSD")] == ["EURUSD"]
    assert [record.trade_id for record in store.list_records(state="blocked")] == [eurusd.trade_id]
    assert store.to_frame(state="blocked")["state"].tolist() == ["blocked"]


def test_memory_store_and_demo_bot_records_blocked_cycle() -> None:
    store = MemoryTradeLifecycleStore()
    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=PaperBroker(initial_balance=10_000),
        risk_manager=RiskManager(RiskConfig(min_confidence=2.0)),
        confluence_config=ConfluenceConfig(min_signal_score=99),
        trade_lifecycle_store=store,
    )

    cycle = bot.run_cycle(make_candles())

    assert cycle.action == "blocked_by_risk"
    assert cycle.trade_lifecycle is not None
    assert cycle.trade_lifecycle.state == "blocked"
    assert store.get(cycle.trade_lifecycle.trade_id) == cycle.trade_lifecycle
    assert store.list_records(state="blocked")[0].history[-1].metadata["source"] == "risk"
