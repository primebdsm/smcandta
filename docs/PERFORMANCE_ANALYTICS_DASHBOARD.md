# Performance Analytics Dashboard

The performance analytics dashboard renders a local HTML page from demo-forward artifacts.

It can read either:

- a single demo-forward report directory containing `summary.json`, `equity_curve.csv`, `trades.csv`, and report tables
- a demo-forward scheduler directory containing `history.csv`, `schedule_summary.json`, and `runs/<run_id>/` report bundles

It does not run a strategy, place orders, or change broker state. It is a reporting layer for operator review.

## CLI

Render from one report bundle:

```bash
python examples/performance_analytics_dashboard.py \
  reports/demo_forward/latest \
  --output reports/performance_analytics/dashboard.html
```

Render from scheduler output:

```bash
python examples/performance_analytics_dashboard.py \
  reports/demo_forward_scheduler/eurusd_m15 \
  --output reports/performance_analytics/eurusd_m15.html \
  --title "EURUSD M15 Performance Analytics"
```

## Python API

```python
from smc_ta import PerformanceAnalyticsDashboardConfig, write_performance_analytics_dashboard

dashboard = write_performance_analytics_dashboard(
    "reports/demo_forward_scheduler/eurusd_m15",
    "reports/performance_analytics/eurusd_m15.html",
    config=PerformanceAnalyticsDashboardConfig(
        title="EURUSD M15 Performance Analytics",
        max_table_rows=25,
    ),
)
```

Use `load_performance_analytics_data(...)` and `render_performance_analytics_dashboard(...)` when a bot or Codex workflow needs to inspect the loaded data before writing HTML.

## What It Shows

- headline metrics: cycles, orders, trades, net PnL, return, drawdown, win rate, profit factor, final equity, blocked cycles
- latest equity curve
- drawdown curve
- setup net PnL bars
- scheduler run final-equity chart
- setup performance table
- session performance table
- daily performance table
- blocked-reason table
- recent trades
- scheduler history and run metrics

## How It Works

1. Detect whether the source is a single report bundle or scheduler root.
2. For scheduler roots, read `history.csv` and find the latest successful or warning report directory.
3. Load the latest report's summary and CSV tables.
4. Read per-run summaries referenced by scheduler history when available.
5. Render a dependency-free HTML dashboard with inline CSS and SVG charts.

## Operational Use

Run this after demo-forward reports or a scheduler cycle. The dashboard helps compare which setups, sessions, and days are contributing to PnL or blocks.

It can improve live-readiness indirectly by making weak setups, bad sessions, excessive blocking, drawdown, and unstable scheduler runs visible before a broker-demo or live-money promotion.
