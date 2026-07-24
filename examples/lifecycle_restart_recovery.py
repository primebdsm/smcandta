"""Recover lifecycle records from broker state after a restart.

OANDA mode reads:
OANDA_ACCOUNT_ID
OANDA_TOKEN
SMC_TA_OANDA_PRACTICE=true|false
"""

from __future__ import annotations

import argparse
import os

import pandas as pd

from smc_ta.broker import OandaBroker, OandaConfig, Position
from smc_ta.lifecycle import (
    LifecycleRecoveryConfig,
    SQLiteTradeLifecycleStore,
    recover_lifecycle_after_restart,
    write_lifecycle_recovery_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover lifecycle state from broker positions after restart")
    parser.add_argument("--broker", choices=["paper", "oanda"], default="paper")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--lifecycle-path", default="trade_lifecycle.sqlite")
    parser.add_argument("--create-missing-lifecycles", action="store_true")
    parser.add_argument("--mark-missing-closed", action="store_true")
    parser.add_argument("--fail-unfilled", action="store_true")
    parser.add_argument("--match-symbol-side", action="store_true")
    parser.add_argument("--output", default=None, help="Optional JSON report path")
    args = parser.parse_args()

    broker = _build_broker(args.broker, args.symbol)
    store = SQLiteTradeLifecycleStore(args.lifecycle_path)
    report = recover_lifecycle_after_restart(
        broker,
        store,
        symbol=args.symbol,
        config=LifecycleRecoveryConfig(
            create_missing_lifecycles_for_broker_positions=args.create_missing_lifecycles,
            mark_missing_broker_positions_closed=args.mark_missing_closed,
            fail_unfilled_lifecycles_without_broker_position=args.fail_unfilled,
            match_unlinked_records_by_symbol_side=args.match_symbol_side,
        ),
    )

    print(f"summary: {report.summary()}")
    _print_frame("actions", report.to_frame())
    _print_frame("lifecycle_records", report.records_frame())
    if args.output:
        output = write_lifecycle_recovery_report(report, args.output)
        print(f"wrote: {output}")
    return 0 if report.ok else 2


def _build_broker(name: str, symbol: str):
    if name == "oanda":
        return OandaBroker(
            OandaConfig(
                account_id=os.environ["OANDA_ACCOUNT_ID"],
                token=os.environ["OANDA_TOKEN"],
                practice=os.environ.get("SMC_TA_OANDA_PRACTICE", "true").lower() != "false",
            )
        )
    return _PaperSnapshotBroker(
        [
            Position(
                position_id="paper-recovered-position",
                symbol=symbol.upper(),
                side="long",
                units=1_000,
                entry_price=1.1000,
                opened_at=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
                stop_loss=1.0950,
                take_profit=1.1100,
            )
        ]
    )


class _PaperSnapshotBroker:
    def __init__(self, positions: list[Position]) -> None:
        self.positions = positions

    def get_open_positions(self, symbol: str | None = None) -> list[Position]:
        symbol_filter = symbol.upper() if symbol else None
        return [position for position in self.positions if symbol_filter is None or position.symbol == symbol_filter]


def _print_frame(label: str, frame: pd.DataFrame) -> None:
    print(f"\n{label}:")
    if frame.empty:
        print("(none)")
    else:
        print(frame.to_string(index=False))


if __name__ == "__main__":
    raise SystemExit(main())
