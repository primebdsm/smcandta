"""Run multi-timeframe confluence from CSV files."""

from __future__ import annotations

import argparse

from smc_ta.data import load_csv_candles
from smc_ta.engine import MultiTimeframeConfig, analyze_multi_timeframe


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--entry-timeframe", default="M15")
    parser.add_argument("--entry-csv", required=True)
    parser.add_argument("--higher", action="append", nargs=2, metavar=("TIMEFRAME", "CSV"))
    args = parser.parse_args()

    candles = {args.entry_timeframe: load_csv_candles(args.entry_csv)}
    for timeframe, path in args.higher or []:
        candles[timeframe] = load_csv_candles(path)

    result = analyze_multi_timeframe(
        candles,
        symbol=args.symbol,
        config=MultiTimeframeConfig(
            entry_timeframe=args.entry_timeframe,
            higher_timeframes=tuple(timeframe for timeframe, _ in args.higher or []),
        ),
    )
    print(result.signals.tail(1).T)
    print(result.setup_classification.tail(1).T)


if __name__ == "__main__":
    main()

