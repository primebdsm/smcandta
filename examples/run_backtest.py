"""Run a spread/slippage-aware backtest from a Forex CSV file."""

from __future__ import annotations

import argparse

from smc_ta.backtest import BacktestConfig, run_backtest
from smc_ta.data import load_csv_candles
from smc_ta.monitoring import performance_summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--spread-pips", type=float, default=1.2)
    parser.add_argument("--slippage-pips", type=float, default=0.1)
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path)
    result = run_backtest(
        candles,
        config=BacktestConfig(
            symbol=args.symbol,
            spread_pips=args.spread_pips,
            slippage_pips=args.slippage_pips,
        ),
    )
    print(performance_summary(result.equity_curve, result.trades))
    print(result.trades.tail())


if __name__ == "__main__":
    main()

