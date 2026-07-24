# Live Dashboard Monitoring

The dashboard module now supports a richer local monitoring view for paper, demo, and broker-integration runs.

It is still dependency-free static HTML. The bot can regenerate the file on each cycle, or a process supervisor can refresh it at an interval.

## Main APIs

```python
from smc_ta import build_live_monitoring_snapshot, write_live_dashboard

snapshot = build_live_monitoring_snapshot(
    symbol="EURUSD",
    signals=result.signals,
    features=result.features,
    account=broker.get_account(),
    open_positions=broker.get_open_positions("EURUSD"),
    equity_curve=equity_curve,
    trades=trades,
    preflight=preflight_report,
    emergency_stop=preflight_report.emergency_stop_result,
    lifecycle_store=lifecycle_store,
    blocked_events=blocked_events,
    execution_samples=oanda_execution_report.execution_frame(),
    mode="demo",
    broker_name="oanda",
)

write_live_dashboard("live_dashboard.html", snapshot, refresh_seconds=30)
```

## Local Example

```bash
python examples/live_dashboard_monitor.py --output live_dashboard.html
```

The example uses deterministic candles and `PaperBroker`; it does not connect to a live broker.

## What The Dashboard Shows

- operational status: `ok`, `warning`, or `blocking`
- account balance, equity, free margin, and drawdown
- latest signal side, confidence, scores, entry, stop, target, and reasons
- SMC/TA context from the latest feature row
- preflight summary and checks
- emergency-stop state
- open positions
- equity curve
- performance metrics
- lifecycle records
- journal events
- blocked events and reasons
- spread/slippage execution samples

## Status Logic

Dashboard status is derived from real project state:

- `blocking`: preflight has blocking checks, emergency stop is active, or health check fails
- `warning`: preflight warnings exist or blocked events are present
- `ok`: no blocking or warning state is present

The dashboard does not place orders and does not change bot decisions. It only renders the state passed into the snapshot.

## Integration Pattern

Recommended loop:

1. On process start, run broker restart sync and preflight.
2. Run analysis on the latest closed candles.
3. Run risk, news, reconciliation, and emergency-stop checks.
4. Save lifecycle and journal events.
5. Build a `LiveMonitoringSnapshot`.
6. Write `live_dashboard.html`.
7. Continue only if execution gates approve the trade.

For OANDA practice validation, pass `report.execution_frame()` from `run_oanda_practice_execution_validation` into `execution_samples`.
