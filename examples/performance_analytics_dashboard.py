"""Render a performance analytics dashboard from demo-forward artifacts."""

from __future__ import annotations

import argparse

from smc_ta import PerformanceAnalyticsDashboardConfig, load_performance_analytics_data, write_performance_analytics_dashboard


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an HTML performance analytics dashboard")
    parser.add_argument("source_dir", help="Demo-forward report directory or scheduler output directory")
    parser.add_argument("--output", default="reports/performance_analytics/dashboard.html")
    parser.add_argument("--title", default="SMC TA Performance Analytics")
    parser.add_argument("--max-table-rows", type=int, default=25)
    parser.add_argument("--refresh-seconds", type=int)
    args = parser.parse_args()

    config = PerformanceAnalyticsDashboardConfig(
        title=args.title,
        max_table_rows=args.max_table_rows,
        refresh_seconds=args.refresh_seconds,
    )
    output = write_performance_analytics_dashboard(args.source_dir, args.output, config=config)
    data = load_performance_analytics_data(args.source_dir)

    print(f"status={data.status}")
    print(f"source_type={data.source_type}")
    print(f"latest_run={data.latest_run_dir}")
    print(f"dashboard={output}")
    return 0 if data.status != "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
