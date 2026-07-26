"""Run non-trading MetaTrader 5 terminal readiness checks.

The local MT5 terminal must be installed and logged into a demo account before
running this command. Optional connection values can be supplied through:

MT5_LOGIN
MT5_PASSWORD
MT5_SERVER
MT5_PATH
"""

from __future__ import annotations

import argparse
import os

from smc_ta.broker import MetaTrader5Broker, Mt5Config


def main() -> None:
    parser = argparse.ArgumentParser(description="Run non-trading MT5 readiness checks")
    parser.add_argument("--symbols", default="EURUSD", help="Comma-separated Forex symbols")
    parser.add_argument("--max-spread-points", type=float, default=None)
    parser.add_argument("--max-tick-age-seconds", type=float, default=15.0)
    parser.add_argument("--allow-real-account", action="store_true")
    parser.add_argument(
        "--symbol-alias",
        action="append",
        default=[],
        help="Map normalized symbol to broker symbol, for example EURUSD=EURUSD.m",
    )
    args = parser.parse_args()

    config = Mt5Config(
        path=os.environ.get("MT5_PATH") or None,
        login=_optional_int(os.environ.get("MT5_LOGIN")),
        password=os.environ.get("MT5_PASSWORD") or None,
        server=os.environ.get("MT5_SERVER") or None,
        symbol_aliases=_symbol_aliases(args.symbol_alias),
        max_spread_points=args.max_spread_points,
        max_tick_age_seconds=args.max_tick_age_seconds,
        allow_real_account=args.allow_real_account,
    )
    broker = MetaTrader5Broker(config=config)
    symbols = tuple(symbol.strip().upper() for symbol in args.symbols.split(",") if symbol.strip())
    report = broker.practice_readiness(symbols)
    print(report.summary())
    frame = report.to_frame()
    if not frame.empty:
        print(frame.to_string(index=False))
    raise SystemExit(0 if report.ok else 2)


def _symbol_aliases(values: list[str]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"symbol alias must use SYMBOL=BROKER_SYMBOL format: {value}")
        symbol, broker_symbol = value.split("=", 1)
        aliases[symbol.strip().upper()] = broker_symbol.strip()
    return aliases


def _optional_int(value: str | None) -> int | None:
    if not value:
        return None
    return int(value)


if __name__ == "__main__":
    main()
