"""Run preflight checks before starting a bot loop."""

from __future__ import annotations

import argparse

from smc_ta import PreflightConfig, RuntimeConfig, run_preflight
from smc_ta.broker import PaperBroker
from smc_ta.data import load_csv_candles
from smc_ta.lifecycle import MemoryTradeLifecycleStore
from smc_ta.safety import EmergencyStopController


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", help="Optional .env-style runtime config")
    parser.add_argument("--csv", help="Optional candle CSV sample")
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()

    runtime = RuntimeConfig.from_env_file(args.env_file) if args.env_file else RuntimeConfig.from_env()
    symbol = (args.symbol or runtime.symbols[0]).upper()
    candles = {symbol: load_csv_candles(args.csv)} if args.csv else None

    broker = PaperBroker(initial_balance=10_000) if runtime.broker == "paper" else None
    lifecycle_store = MemoryTradeLifecycleStore() if runtime.broker == "paper" else None
    emergency_stop = EmergencyStopController() if runtime.mode in {"paper", "demo", "live"} else None

    report = run_preflight(
        runtime_config=runtime,
        candles_by_symbol=candles,
        broker=broker,
        emergency_stop=emergency_stop,
        lifecycle_store=lifecycle_store,
        config=PreflightConfig(require_candles=args.csv is not None),
    )
    print(report.summary())
    frame = report.to_frame()
    if not frame.empty:
        print(frame.to_string(index=False))
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
