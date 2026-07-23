from __future__ import annotations

import pandas as pd

from smc_ta.broker import OrderRequest, PaperBroker, Position
from smc_ta.live import DemoTradingBot
from smc_ta.reconciliation import (
    BrokerReconciler,
    MemoryPositionLedger,
    ReconciliationConfig,
    SQLitePositionLedger,
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


def make_candles(n: int = 160) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + (pd.RangeIndex(n).to_series().to_numpy() * 0.00001), index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100,
            "spread": 0.0001,
        },
        index=index,
    )


def test_reconciler_accepts_matching_positions() -> None:
    expected = position()
    broker = position()
    result = BrokerReconciler().reconcile([broker], [expected])

    assert result.ok
    assert result.summary() == "reconciliation_ok"


def test_reconciler_blocks_unmanaged_missing_and_mismatch() -> None:
    reconciler = BrokerReconciler()

    unmanaged = reconciler.reconcile([position("broker_only")], [])
    assert not unmanaged.ok
    assert "unmanaged_broker_position" in unmanaged.blocking_reasons

    missing = reconciler.reconcile([], [position("expected_only")])
    assert not missing.ok
    assert "missing_broker_position" in missing.blocking_reasons

    mismatch = reconciler.reconcile([position(units=20_000)], [position(units=10_000)])
    assert not mismatch.ok
    assert "units_mismatch" in mismatch.blocking_reasons


def test_reconciler_duplicate_detection_can_warn_instead_of_blocking() -> None:
    reconciler = BrokerReconciler(
        config=ReconciliationConfig(
            max_positions_per_symbol_side=1,
            block_on_unmanaged_broker_positions=False,
        )
    )
    result = reconciler.reconcile([position("p1"), position("p2")], [])

    assert not result.ok
    assert "duplicate_broker_positions" in result.blocking_reasons
    assert any(issue.kind == "unmanaged_broker_position" and issue.severity == "warning" for issue in result.issues)


def test_memory_and_sqlite_ledgers_persist_expected_positions(tmp_path) -> None:
    memory = MemoryPositionLedger([position()])
    assert len(memory.open_positions("EURUSD")) == 1
    memory.record_closed_position("p1", exit_price=1.1010, closed_at=pd.Timestamp("2024-01-02", tz="UTC"))
    assert not memory.open_positions("EURUSD")

    sqlite = SQLitePositionLedger(tmp_path / "positions.sqlite")
    sqlite.record_open_position(position("sqlite_p1"))
    assert sqlite.open_positions("EURUSD")[0].position_id == "sqlite_p1"
    sqlite.record_closed_position("sqlite_p1", exit_price=1.1010)
    assert not sqlite.open_positions("EURUSD")


def test_demo_bot_blocks_when_broker_state_is_not_reconciled() -> None:
    broker = PaperBroker(initial_balance=10_000)
    broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=10_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )
    bot = DemoTradingBot(
        symbol="EURUSD",
        broker=broker,
        risk_manager=RiskManager(RiskConfig(min_confidence=0.0, min_reward_to_risk=1.0)),
        reconciler=BrokerReconciler(MemoryPositionLedger()),
    )

    result = bot.run_cycle(make_candles())

    assert result.action == "blocked_by_reconciliation"
    assert result.reconciliation_result is not None
    assert "unmanaged_broker_position" in result.reconciliation_result.blocking_reasons

