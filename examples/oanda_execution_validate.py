"""Validate OANDA practice execution with guarded minimum-size demo trades.

Set environment variables:
OANDA_ACCOUNT_ID
OANDA_TOKEN

By default this runs a dry-run plan only. Pass --execute to place and close
minimum-size practice orders.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from smc_ta.broker import (
    OandaBroker,
    OandaConfig,
    OandaExecutionValidationConfig,
    run_oanda_practice_execution_validation,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--side", choices=("buy", "sell"), default="buy")
    parser.add_argument("--units", type=float)
    parser.add_argument("--stop-loss-pips", type=float, default=20.0)
    parser.add_argument("--take-profit-pips", type=float, default=20.0)
    parser.add_argument("--max-spread-pips", type=float, default=None)
    parser.add_argument("--max-price-age-seconds", type=float, default=15.0)
    parser.add_argument("--max-order-slippage-pips", type=float, default=1.0)
    parser.add_argument("--ledger-path", default=".oanda_execution_validation.sqlite")
    parser.add_argument("--allow-existing-positions", action="store_true")
    parser.add_argument("--skip-rejected-order", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Place and close minimum-size OANDA practice orders")
    parser.add_argument("--output-dir", help="Optional folder for CSV reports")
    args = parser.parse_args()

    broker = OandaBroker(
        OandaConfig(
            account_id=os.environ["OANDA_ACCOUNT_ID"],
            token=os.environ["OANDA_TOKEN"],
            practice=True,
            max_spread_pips=args.max_spread_pips,
            max_price_age_seconds=args.max_price_age_seconds,
            max_order_slippage_pips=args.max_order_slippage_pips,
        )
    )
    config = OandaExecutionValidationConfig(
        symbol=args.symbol,
        side=args.side,
        units=args.units,
        stop_loss_pips=args.stop_loss_pips,
        take_profit_pips=args.take_profit_pips,
        ledger_path=args.ledger_path,
        run_rejected_order=not args.skip_rejected_order,
        allow_existing_positions=args.allow_existing_positions,
    )
    report = run_oanda_practice_execution_validation(broker, config=config, execute=args.execute)
    print(report.summary())
    checks = report.to_frame()
    if not checks.empty:
        print(checks.to_string(index=False))
    executions = report.execution_frame()
    if not executions.empty:
        print(executions.to_string(index=False))
    if args.output_dir:
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True)
        checks.to_csv(output / "oanda_execution_checks.csv", index=False)
        executions.to_csv(output / "oanda_execution_spread_slippage.csv", index=False)
    raise SystemExit(0 if report.ok else 2)


if __name__ == "__main__":
    main()
