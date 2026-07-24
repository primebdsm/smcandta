"""Render an upgraded local live-monitoring dashboard from sample data."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from smc_ta import (
    RuntimeConfig,
    build_live_monitoring_snapshot,
    run_backtest,
    run_preflight,
    write_live_dashboard,
    write_monitoring_snapshot_json,
)
from smc_ta.backtest import BacktestConfig
from smc_ta.broker import OrderRequest, PaperBroker
from smc_ta.lifecycle import MemoryTradeLifecycleStore, TradeLifecycleStateMachine
from smc_ta.safety import EmergencyStopController


def make_candles(rows: int = 180) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    wave = np.sin(np.arange(rows) / 8) * 0.001
    drift = np.arange(rows) * 0.00001
    close = pd.Series(1.1000 + wave + drift, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.00035
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.00035
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(rows) % 30),
            "spread": 0.00012,
        },
        index=index,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output", default="live_dashboard.html")
    parser.add_argument("--snapshot-output")
    parser.add_argument("--refresh-seconds", type=int)
    args = parser.parse_args()

    candles = make_candles()
    backtest = run_backtest(candles, config=BacktestConfig(symbol=args.symbol))
    broker = PaperBroker(initial_balance=10_000)
    market_price = float(candles["close"].iloc[-1])
    broker.place_order(
        OrderRequest(
            symbol=args.symbol,
            side="buy",
            units=1_000,
            stop_loss=market_price - 0.002,
            take_profit=market_price + 0.004,
        ),
        market_price=market_price,
    )

    emergency_stop = EmergencyStopController()
    lifecycle_store = MemoryTradeLifecycleStore()
    lifecycle = TradeLifecycleStateMachine()
    latest_signal = backtest.signals.iloc[-1].copy()
    latest_signal["side"] = "long"
    latest_signal["confidence"] = 0.72
    record = lifecycle.create_from_signal(
        symbol=args.symbol,
        timestamp=candles.index[-1],
        signal=latest_signal,
        setup_name="dashboard_sample",
    )
    record = lifecycle.block(record, "sample_risk_block", source="dashboard")
    lifecycle_store.save(record)

    runtime = RuntimeConfig(mode="paper", broker="paper", symbols=(args.symbol,), timeframes=("M15",))
    preflight = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={args.symbol: candles.tail(60)},
        broker=broker,
        emergency_stop=emergency_stop,
        lifecycle_store=lifecycle_store,
    )
    blocked_events = pd.DataFrame(
        [
            {
                "timestamp": candles.index[-1],
                "symbol": args.symbol,
                "source": "dashboard",
                "reason": "sample_risk_block",
            }
        ]
    )
    fill = broker.fills[-1]
    execution_samples = pd.DataFrame(
        [
            {
                "label": "paper_open",
                "side": "buy",
                "units": 1000,
                "reference_price": market_price,
                "fill_price": fill.price,
                "spread_pips": fill.spread / 0.0001,
                "slippage_pips": fill.slippage / 0.0001,
            }
        ]
    )
    snapshot = build_live_monitoring_snapshot(
        symbol=args.symbol,
        signals=backtest.signals,
        features=backtest.features,
        account=broker.get_account(),
        open_positions=broker.get_open_positions(args.symbol),
        equity_curve=backtest.equity_curve,
        trades=backtest.trades,
        blocked_events=blocked_events,
        preflight=preflight,
        emergency_stop=preflight.emergency_stop_result,
        lifecycle_store=lifecycle_store,
        execution_samples=execution_samples,
        mode="paper",
        broker_name="paper",
    )
    output = write_live_dashboard(args.output, snapshot, refresh_seconds=args.refresh_seconds)
    print(output)
    if args.snapshot_output:
        snapshot_output = write_monitoring_snapshot_json(snapshot, args.snapshot_output)
        print(snapshot_output)


if __name__ == "__main__":
    main()
