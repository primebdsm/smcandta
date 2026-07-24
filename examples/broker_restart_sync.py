"""Run broker restart synchronization before a live/demo bot loop.

OANDA mode reads:
OANDA_ACCOUNT_ID
OANDA_TOKEN
SMC_TA_OANDA_PRACTICE=true|false
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from smc_ta.broker import OandaBroker, OandaConfig, OrderRequest, PaperBroker
from smc_ta.reconciliation import (
    RestartSyncConfig,
    SQLitePositionLedger,
    SQLiteSyncCheckpointStore,
    sync_broker_state_after_restart,
    write_restart_sync_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync broker state after process restart")
    parser.add_argument("--broker", choices=["paper", "oanda"], default="paper")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--ledger-path", default="restart_sync_positions.sqlite")
    parser.add_argument("--checkpoint-path", default=None)
    parser.add_argument("--adopt-unmanaged", action="store_true")
    parser.add_argument("--mark-missing-closed", action="store_true")
    parser.add_argument("--update-mismatches", action="store_true")
    parser.add_argument("--allow-unlinked-pending-orders", action="store_true")
    parser.add_argument("--no-transactions", action="store_true")
    parser.add_argument("--no-pending-orders", action="store_true")
    parser.add_argument("--output", default=None, help="Optional JSON report path")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    broker = _build_broker(args.broker, symbol)
    ledger = SQLitePositionLedger(args.ledger_path)
    checkpoint_path = Path(args.checkpoint_path or args.ledger_path)
    checkpoint_store = SQLiteSyncCheckpointStore(checkpoint_path)

    report = sync_broker_state_after_restart(
        broker,
        ledger,
        symbol=symbol,
        checkpoint_store=checkpoint_store,
        config=RestartSyncConfig(
            adopt_unmanaged_broker_positions=args.adopt_unmanaged,
            mark_missing_expected_positions_closed=args.mark_missing_closed,
            update_mismatched_expected_positions=args.update_mismatches,
            fetch_broker_transactions=not args.no_transactions,
            fetch_pending_orders=not args.no_pending_orders,
            block_on_unlinked_pending_orders=not args.allow_unlinked_pending_orders,
        ),
    )

    print(f"summary: {report.summary()}")
    print(f"before_ok: {report.before_reconciliation.ok}")
    print(f"after_ok: {report.after_reconciliation.ok}")
    print(f"previous_transaction_id: {report.previous_transaction_id}")
    print(f"latest_transaction_id: {report.latest_transaction_id}")
    _print_frame("actions", report.to_frame())
    _print_frame("pending_orders", report.orders_frame())
    _print_frame("transactions", report.transactions_frame())

    if args.output:
        output = write_restart_sync_report(report, args.output)
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
    broker = PaperBroker(initial_balance=10_000)
    broker.place_order(
        OrderRequest(symbol=symbol, side="buy", units=1_000),
        market_price=1.1000,
        timestamp=pd.Timestamp("2024-01-01", tz="UTC").to_pydatetime(),
    )
    return broker


def _print_frame(label: str, frame: pd.DataFrame) -> None:
    print(f"\n{label}:")
    if frame.empty:
        print("(none)")
    else:
        print(frame.to_string(index=False))


if __name__ == "__main__":
    raise SystemExit(main())
