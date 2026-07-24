"""Run the integrated OANDA practice startup and monitoring drill."""

from __future__ import annotations

import argparse

from smc_ta import PracticeStartupRunConfig, run_practice_startup_monitoring


def main() -> int:
    parser = argparse.ArgumentParser(description="Run startup sync, recovery, preflight, status, and dashboard reports")
    parser.add_argument("--broker", choices=("paper", "oanda"), default="paper")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--output-dir", default="reports/practice_startup")
    parser.add_argument("--env-file")
    parser.add_argument("--csv")
    parser.add_argument("--candle-limit", type=int, default=200)
    parser.add_argument("--max-spread-pips", type=float)
    parser.add_argument("--max-price-age-seconds", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--ledger-path")
    parser.add_argument("--checkpoint-path")
    parser.add_argument("--lifecycle-path")
    parser.add_argument("--adopt-unmanaged", action="store_true")
    parser.add_argument("--mark-missing-positions-closed", action="store_true")
    parser.add_argument("--update-mismatches", action="store_true")
    parser.add_argument("--allow-unlinked-pending-orders", action="store_true")
    parser.add_argument("--create-missing-lifecycles", action="store_true")
    parser.add_argument("--mark-missing-lifecycles-closed", action="store_true")
    parser.add_argument("--fail-unfilled-lifecycles", action="store_true")
    parser.add_argument("--match-lifecycle-symbol-side", action="store_true")
    parser.add_argument("--no-memory-alert-probe", action="store_true")
    parser.add_argument("--no-incident-on-failure", action="store_true")
    args = parser.parse_args()

    result = run_practice_startup_monitoring(
        PracticeStartupRunConfig(
            broker=args.broker,
            symbol=args.symbol,
            timeframe=args.timeframe,
            output_dir=args.output_dir,
            env_file=args.env_file,
            candle_csv=args.csv,
            candle_limit=args.candle_limit,
            max_spread_pips=args.max_spread_pips,
            max_price_age_seconds=args.max_price_age_seconds,
            timeout=args.timeout,
            ledger_path=args.ledger_path,
            checkpoint_path=args.checkpoint_path,
            lifecycle_path=args.lifecycle_path,
            adopt_unmanaged_positions=args.adopt_unmanaged,
            mark_missing_positions_closed=args.mark_missing_positions_closed,
            update_mismatched_positions=args.update_mismatches,
            allow_unlinked_pending_orders=args.allow_unlinked_pending_orders,
            create_missing_lifecycles=args.create_missing_lifecycles,
            mark_missing_lifecycles_closed=args.mark_missing_lifecycles_closed,
            fail_unfilled_lifecycles=args.fail_unfilled_lifecycles,
            match_lifecycle_symbol_side=args.match_lifecycle_symbol_side,
            probe_memory_alert=not args.no_memory_alert_probe,
            write_incident_on_failure=not args.no_incident_on_failure,
        )
    )

    print(result.summary())
    for name, path in sorted(result.artifacts.items()):
        print(f"{name}={path}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
