"""Analyze a Forex CSV file with the combined SMC + TA engine.

Expected CSV columns:
time, open, high, low, close, tick_volume, spread
"""

from __future__ import annotations

import argparse

import pandas as pd

from smc_ta import analyze_forex


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    args = parser.parse_args()

    candles = pd.read_csv(args.csv_path, parse_dates=["time"], index_col="time")
    result = analyze_forex(candles, symbol=args.symbol)

    latest = result.signals.iloc[-1]
    print(latest[["side", "confidence", "long_score", "short_score", "reasons"]])
    print()
    print("Active feature snapshot:")
    print(result.features.tail(1).T.dropna().tail(40))


if __name__ == "__main__":
    main()

