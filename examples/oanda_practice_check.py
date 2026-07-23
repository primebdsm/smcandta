"""Run non-trading OANDA practice readiness checks.

Set environment variables:
OANDA_ACCOUNT_ID
OANDA_TOKEN
"""

from __future__ import annotations

import argparse
import os

from smc_ta.broker import OandaBroker, OandaConfig


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", default="EURUSD", help="Comma-separated Forex symbols")
    parser.add_argument("--max-spread-pips", type=float, default=None)
    parser.add_argument("--max-price-age-seconds", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    config = OandaConfig(
        account_id=os.environ["OANDA_ACCOUNT_ID"],
        token=os.environ["OANDA_TOKEN"],
        practice=True,
        timeout=args.timeout,
        max_spread_pips=args.max_spread_pips,
        max_price_age_seconds=args.max_price_age_seconds,
    )
    broker = OandaBroker(config)
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    report = broker.practice_readiness(symbols)
    print(report.summary())
    frame = report.to_frame()
    if not frame.empty:
        print(frame.to_string(index=False))
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
