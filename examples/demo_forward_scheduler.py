"""Run demo-forward reports on a schedule."""

from __future__ import annotations

import argparse

from smc_ta import DemoForwardConfig, DemoForwardScheduleConfig, run_demo_forward_schedule
from smc_ta.risk import RiskConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate timestamped demo-forward report bundles on a schedule")
    parser.add_argument("csv_path", nargs="?", help="Optional OHLCV CSV. If omitted, a deterministic sample is used.")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output-dir", default="reports/demo_forward_scheduler")
    parser.add_argument("--interval-seconds", type=float, default=900.0)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--loop", action="store_true", help="Run continuously until the process is stopped.")
    parser.add_argument("--no-skip-unchanged", action="store_true")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--run-label-prefix", default="demo_forward")
    parser.add_argument("--warmup-candles", type=int, default=120)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--initial-balance", type=float, default=10_000)
    parser.add_argument("--spread-pips", type=float, default=1.2)
    parser.add_argument("--slippage-pips", type=float, default=0.1)
    parser.add_argument("--commission-per-order", type=float, default=0.0)
    parser.add_argument("--risk-percent", type=float, default=0.5)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--min-rr", type=float, default=1.0)
    parser.add_argument("--max-units", type=float, default=10_000)
    args = parser.parse_args()

    result = run_demo_forward_schedule(
        DemoForwardScheduleConfig(
            output_dir=args.output_dir,
            csv_path=args.csv_path,
            interval_seconds=args.interval_seconds,
            max_runs=None if args.loop else args.runs,
            skip_when_no_new_candle=not args.no_skip_unchanged,
            stop_on_failure=args.stop_on_failure,
            run_label_prefix=args.run_label_prefix,
            demo_forward=DemoForwardConfig(
                symbol=args.symbol,
                initial_balance=args.initial_balance,
                warmup_candles=args.warmup_candles,
                max_cycles=args.max_cycles,
                default_spread_pips=args.spread_pips,
                slippage_pips=args.slippage_pips,
                commission_per_order=args.commission_per_order,
                risk=RiskConfig(
                    risk_percent_per_trade=args.risk_percent,
                    min_confidence=args.min_confidence,
                    min_reward_to_risk=args.min_rr,
                    max_units=args.max_units,
                ),
            ),
        )
    )

    print(result.summary())
    print(f"history={result.history_csv}")
    print(f"summary_json={result.summary_json}")
    for run in result.runs[-5:]:
        print(f"{run.run_id}={run.status}:{run.message}")
        if run.html_report is not None:
            print(f"{run.run_id}.report={run.html_report}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
