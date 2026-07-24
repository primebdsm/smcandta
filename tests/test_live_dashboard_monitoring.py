from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta import (
    RuntimeConfig,
    build_live_monitoring_snapshot,
    check_broker_connectivity,
    probe_alert_channel,
    run_backtest,
    run_preflight,
    write_dashboard,
    write_live_dashboard,
)
from smc_ta.backtest import BacktestConfig
from smc_ta.broker import OrderRequest, PaperBroker
from smc_ta.dashboard import render_live_dashboard_html
from smc_ta.lifecycle import MemoryTradeLifecycleStore, TradeLifecycleStateMachine
from smc_ta.safety import EmergencyStopController


def make_candles(rows: int = 80) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    wave = np.sin(np.arange(rows) / 6) * 0.0008
    close = pd.Series(1.1000 + wave + np.arange(rows) * 0.00001, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0003
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0003
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(rows) % 20),
            "spread": 0.0001,
        },
        index=index,
    )


def build_sample_snapshot():
    candles = make_candles()
    backtest = run_backtest(candles, config=BacktestConfig(symbol="EURUSD"))
    broker = PaperBroker(initial_balance=10_000)
    market_price = float(candles["close"].iloc[-1])
    broker.place_order(
        OrderRequest(symbol="EURUSD", side="buy", units=1_000, stop_loss=1.095, take_profit=1.11),
        market_price=market_price,
    )
    emergency_stop = EmergencyStopController()
    lifecycle_store = MemoryTradeLifecycleStore()
    lifecycle = TradeLifecycleStateMachine()
    signal = backtest.signals.iloc[-1].copy()
    signal["side"] = "long"
    signal["confidence"] = 0.75
    record = lifecycle.create_from_signal(
        symbol="EURUSD",
        timestamp=candles.index[-1],
        signal=signal,
        setup_name="test_setup",
    )
    lifecycle_store.save(lifecycle.block(record, "test_block", source="test"))
    preflight = run_preflight(
        runtime_config=RuntimeConfig(mode="paper", broker="paper", symbols=("EURUSD",), timeframes=("M15",)),
        candles_by_symbol={"EURUSD": candles.tail(40)},
        broker=broker,
        emergency_stop=emergency_stop,
        lifecycle_store=lifecycle_store,
    )
    blocked_events = pd.DataFrame(
        [{"timestamp": candles.index[-1], "symbol": "EURUSD", "reason": "test_block"}]
    )
    execution_samples = pd.DataFrame(
        [{"label": "paper_open", "side": "buy", "units": 1000, "spread_pips": 1.0, "slippage_pips": 0.1}]
    )
    broker_status = check_broker_connectivity(broker, broker_name="paper", symbol="EURUSD")
    alert_status = probe_alert_channel(_MemoryAlert(), channel_name="memory")
    return build_live_monitoring_snapshot(
        symbol="EURUSD",
        signals=backtest.signals,
        features=backtest.features,
        account=broker.get_account(),
        open_positions=broker.get_open_positions("EURUSD"),
        equity_curve=backtest.equity_curve,
        trades=backtest.trades,
        preflight=preflight,
        emergency_stop=preflight.emergency_stop_result,
        lifecycle_store=lifecycle_store,
        blocked_events=blocked_events,
        execution_samples=execution_samples,
        broker_connectivity=(broker_status,),
        alert_delivery=(alert_status,),
        mode="paper",
        broker_name="paper",
    )


def test_live_monitoring_snapshot_collects_operational_state() -> None:
    snapshot = build_sample_snapshot()

    assert snapshot.symbol == "EURUSD"
    assert snapshot.status == "warning"
    assert snapshot.open_position_count == 1
    assert snapshot.active_lifecycle_count == 0
    assert snapshot.account_dict()["equity"] > 0
    assert not snapshot.positions_frame().empty
    assert not snapshot.lifecycle_frame().empty
    assert not snapshot.preflight_frame().empty
    assert not snapshot.broker_connectivity_frame().empty
    assert not snapshot.alert_delivery_frame().empty
    assert "blocked_events_present" in snapshot.warning_reasons


def test_live_dashboard_html_contains_core_sections() -> None:
    snapshot = build_sample_snapshot()

    html = render_live_dashboard_html(snapshot, refresh_seconds=30)

    assert "EURUSD Live Monitor" in html
    assert "Current Signal" in html
    assert "Open Positions" in html
    assert "Preflight Checks" in html
    assert "Broker Connectivity" in html
    assert "Alert Delivery" in html
    assert "Execution Samples" in html
    assert 'http-equiv="refresh"' in html
    assert "paper_open" in html
    assert "<svg" in html


def test_write_live_dashboard_and_legacy_write_dashboard(tmp_path) -> None:
    snapshot = build_sample_snapshot()
    live_path = write_live_dashboard(tmp_path / "live.html", snapshot)
    legacy_path = write_dashboard(
        tmp_path / "legacy.html",
        symbol=snapshot.symbol,
        signals=pd.DataFrame([snapshot.latest_signal]),
        features=pd.DataFrame([snapshot.latest_features]),
        equity_curve=snapshot.equity_curve,
        trades=snapshot.trades,
        account=snapshot.account,
        open_positions=snapshot.open_positions,
        preflight=snapshot.preflight,
        emergency_stop=snapshot.emergency_stop,
        lifecycle_records=snapshot.lifecycle_records,
        blocked_events=snapshot.blocked_events,
        execution_samples=snapshot.execution_samples,
    )

    assert live_path.exists()
    assert legacy_path.exists()
    assert "Safety State" in live_path.read_text(encoding="utf-8")
    assert "Safety State" in legacy_path.read_text(encoding="utf-8")


class _MemoryAlert:
    def send(self, message: str) -> None:
        self.message = message
