"""Render a local static dashboard from a backtest."""

from __future__ import annotations

import argparse

from smc_ta.backtest import BacktestConfig, run_backtest
from smc_ta.dashboard import write_dashboard
from smc_ta.data import load_csv_candles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output", default="dashboard.html")
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path)
    result = run_backtest(candles, config=BacktestConfig(symbol=args.symbol))
    output = write_dashboard(
        args.output,
        symbol=args.symbol,
        signals=result.signals,
        features=result.features,
        equity_curve=result.equity_curve,
        trades=result.trades,
    )
    print(output)


if __name__ == "__main__":
    main()

