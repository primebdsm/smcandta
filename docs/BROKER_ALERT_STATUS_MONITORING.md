# Broker And Alert Status Monitoring

The monitoring layer can now show broker connectivity and alert delivery status in the local dashboard, hosted monitor JSON, and incident bundles.

These checks are operational controls. They do not generate signals, place orders, close positions, or reset safety controls.

## Main APIs

```python
from smc_ta import (
    check_broker_connectivity,
    probe_alert_channel,
    build_live_monitoring_snapshot,
)
```

## Broker Connectivity Probe

```python
broker_status = check_broker_connectivity(
    broker,
    broker_name="oanda",
    symbol="EURUSD",
    include_transactions=True,
    include_pending_orders=True,
)
```

The probe calls read-only broker methods:

- `get_account()`
- `get_open_positions(symbol)`
- optional `get_latest_transaction_id()`
- optional `get_pending_orders(symbol=...)`

Required account or position probe failures produce `blocking` status. Optional transaction or pending-order probe failures produce `warning` status.

## Alert Delivery Probe

```python
alert_status = probe_alert_channel(
    telegram_alert,
    channel_name="telegram",
    message="SMC TA alert delivery probe",
)
```

This sends one explicit test message through the supplied channel.

By default, alert failures are `warning` because they affect operator visibility but do not prove broker state is unsafe. Use `blocking_on_failure=True` if your deployment policy requires alerts to work before trading.

## Dashboard Integration

```python
snapshot = build_live_monitoring_snapshot(
    symbol="EURUSD",
    account=broker.get_account(),
    open_positions=broker.get_open_positions("EURUSD"),
    broker_connectivity=(broker_status,),
    alert_delivery=(alert_status,),
    mode="demo",
    broker_name="oanda",
)
```

The dashboard renders:

- Broker Connectivity panel
- Alert Delivery panel
- broker/alert summaries inside Safety State
- broker blocking reasons in dashboard status
- alert warning/blocking reasons in dashboard status

## Hosted Monitor

`write_monitoring_snapshot_json` includes:

- `broker_connectivity`
- `alert_delivery`

These fields are served through `/status.json` and `/snapshot.json` by `examples/serve_monitoring.py`.

## Incident Bundles

When an incident bundle receives a monitoring snapshot, it writes:

- `monitoring_broker_connectivity.csv`
- `monitoring_alert_delivery.csv`

This helps review whether a blocked startup was caused by broker connectivity, alert delivery, or another safety gate.

## Operational Policy

Recommended policy:

- broker account/position probe failure: block trading
- broker optional transaction/pending-order probe failure: warning, then investigate
- alert probe failure: warning by default
- alert probe failure with required production alerts: block trading
- stale or missing dashboard/snapshot: incident procedure

If broker connectivity is blocking, do not trust local state until broker platform state is checked manually.
