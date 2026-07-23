"""Validate a Forex candle CSV before analysis or backtesting."""

from __future__ import annotations

import argparse

from smc_ta.data import DataQualityConfig, load_and_validate_csv_candles


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--max-spread-pips", type=float, default=5.0)
    args = parser.parse_args()

    _, report = load_and_validate_csv_candles(
        args.csv_path,
        config=DataQualityConfig(
            symbol=args.symbol,
            timeframe=args.timeframe,
            max_spread_pips=args.max_spread_pips,
        ),
    )
    print(report.summary())
    print(report.to_frame())
    raise SystemExit(0 if report.ok else 1)


if __name__ == "__main__":
    main()

