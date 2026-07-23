"""Download OANDA candles into the normalized project format.

Set environment variables:
OANDA_ACCOUNT_ID
OANDA_TOKEN
"""

from __future__ import annotations

import argparse
import os

from smc_ta.broker import OandaCandleDataSource, OandaConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--count", type=int, default=500)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    source = OandaCandleDataSource(
        OandaConfig(
            account_id=os.environ["OANDA_ACCOUNT_ID"],
            token=os.environ["OANDA_TOKEN"],
            practice=not args.live,
        )
    )
    candles = source.get_candles(args.symbol, args.timeframe, limit=args.count)
    if args.output:
        candles.reset_index(names="time").to_csv(args.output, index=False)
    else:
        print(candles.tail())


if __name__ == "__main__":
    main()

