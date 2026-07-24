# Live Dashboard Monitoring

The dashboard module now supports a richer local monitoring view for paper, demo, and broker-integration runs.

It is still dependency-free static HTML. The bot can regenerate the file on each cycle, or a process supervisor can refresh it at an interval.

## Main APIs

```python
from smc_ta import build_live_monitoring_snapshot, write_live_dashboard, write_monitoring_snapshot_json

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
    broker_connectivity=(broker_status,),
    alert_delivery=(alert_status,),
    mode="demo",
    broker_name="oanda",
)

write_live_dashboard("live_dashboard.html", snapshot, refresh_seconds=30)
write_monitoring_snapshot_json(snapshot, "monitoring_snapshot.json")
```

## Local Example

```bash
python examples/live_dashboard_monitor.py --output live_dashboard.html --snapshot-output monitoring_snapshot.json
```

The example uses deterministic candles and `PaperBroker`; it does not connect to a live broker.

## Hosted Example

```bash
export SMC_TA_MONITOR_PASSWORD="change-me"

python examples/serve_monitoring.py \
  --dashboard live_dashboard.html \
  --snapshot monitoring_snapshot.json \
  --host 127.0.0.1 \
  --port 8080 \
  --username admin
```

The hosted monitor serves `/dashboard`, `/status.json`, `/snapshot.json`, `/healthz`, and optional `/artifacts/<path>`. Keep auth enabled, and put HTTPS, VPN, or an SSH tunnel in front of it before exposing it outside localhost. See `docs/HOSTED_MONITORING.md`.

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
- broker connectivity status
- alert delivery status

## Status Logic

Dashboard status is derived from real project state:

- `blocking`: preflight has blocking checks, emergency stop is active, health check fails, broker connectivity blocks, or an alert probe is configured as blocking
- `warning`: preflight warnings exist, blocked events are present, optional broker probes fail, or alert delivery fails with default warning policy
- `ok`: no blocking or warning state is present

The dashboard does not place orders and does not change bot decisions. It only renders the state passed into the snapshot.

If the dashboard is stale or shows `blocking`, treat it as an operations incident until broker state is checked manually. See `docs/INCIDENT_PROCEDURES.md`.

## Integration Pattern

Recommended loop:

1. On process start, run broker restart sync, lifecycle restart recovery, and preflight.
2. Run analysis on the latest closed candles.
3. Run risk, news, reconciliation, and emergency-stop checks.
4. Save lifecycle and journal events.
5. Build a `LiveMonitoringSnapshot`.
6. Write `live_dashboard.html`.
7. Write `monitoring_snapshot.json` if using hosted monitoring.
8. Continue only if execution gates approve the trade.

For OANDA practice validation, pass `report.execution_frame()` from `run_oanda_practice_execution_validation` into `execution_samples`.

For deployment order, see `docs/DEPLOYMENT_RUNBOOK.md`.

For broker connectivity and alert delivery panels, see `docs/BROKER_ALERT_STATUS_MONITORING.md`.
