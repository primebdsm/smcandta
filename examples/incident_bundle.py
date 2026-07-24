"""Create a sample operations incident bundle."""

from __future__ import annotations

import argparse

import pandas as pd

from smc_ta import RuntimeConfig, build_live_monitoring_snapshot, run_preflight, write_incident_report_bundle
from smc_ta.broker import OrderRequest, PaperBroker
from smc_ta.lifecycle import MemoryTradeLifecycleStore
from smc_ta.safety import EmergencyStopController


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="reports/incidents/sample")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--severity", default="SEV2")
    parser.add_argument("--title", default="sample emergency stop incident")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    runtime = RuntimeConfig(mode="paper", broker="paper", symbols=(symbol,), timeframes=("M15",))
    broker = PaperBroker(initial_balance=10_000)
    broker.place_order(
        OrderRequest(symbol=symbol, side="buy", units=1_000, stop_loss=1.0950, take_profit=1.1100),
        market_price=1.1000,
    )
    emergency_stop = EmergencyStopController()
    emergency_stop_result = emergency_stop.activate("sample_operator_stop")
    lifecycle_store = MemoryTradeLifecycleStore()
    candles = _sample_candles()
    preflight = run_preflight(
        runtime_config=runtime,
        candles_by_symbol={symbol: candles},
        broker=broker,
        emergency_stop=emergency_stop,
        lifecycle_store=lifecycle_store,
    )
    journal_events = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp.now(tz="UTC"),
                "symbol": symbol,
                "event": "sample_incident_bundle",
                "reason": "operator captured incident evidence",
            }
        ]
    )
    snapshot = build_live_monitoring_snapshot(
        symbol=symbol,
        account=broker.get_account(),
        open_positions=broker.get_open_positions(symbol),
        preflight=preflight,
        emergency_stop=emergency_stop_result,
        lifecycle_store=lifecycle_store,
        journal_events=journal_events,
        mode="paper",
        broker_name="paper",
    )
    bundle = write_incident_report_bundle(
        args.output_dir,
        title=args.title,
        severity=args.severity,
        symbol=symbol,
        runtime_config=runtime,
        monitoring_snapshot=snapshot,
        notes=("sample bundle generated from paper objects",),
        operator_actions=("manual emergency stop activated", "incident evidence captured"),
    )

    print(f"incident_bundle_written={bundle.output_dir}")
    print(f"summary_json={bundle.summary_json}")
    print(f"markdown_report={bundle.markdown_report}")


def _sample_candles() -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=40, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + (pd.RangeIndex(40) * 0.00002), index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0002
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0002
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


if __name__ == "__main__":
    main()
