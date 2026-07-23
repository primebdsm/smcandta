"""Render a standalone SMC/TA analysis chart from CSV candles."""

from __future__ import annotations

import argparse

from smc_ta import ChartConfig, analyze_forex, write_analysis_chart
from smc_ta.data import load_csv_candles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output", default="analysis_chart.html")
    parser.add_argument("--visible-bars", type=int, default=160)
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path)
    result = analyze_forex(candles, symbol=args.symbol)
    output = write_analysis_chart(
        args.output,
        result,
        symbol=args.symbol,
        config=ChartConfig(visible_bars=args.visible_bars),
    )
    print(output)


if __name__ == "__main__":
    main()
