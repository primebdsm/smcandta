# Broker Transaction Stream Panel

The broker transaction stream panel shows recent broker-side account transactions in the live dashboard, hosted snapshot JSON, practice startup reports, and incident bundles.

It is read-only. It does not place orders, close positions, cancel orders, or repair state.

## Main API

```python
from smc_ta import broker_transaction_stream_frame, build_live_monitoring_snapshot

transactions = broker_transaction_stream_frame(raw_broker_transactions, symbol="EURUSD")

snapshot = build_live_monitoring_snapshot(
    symbol="EURUSD",
    broker_transactions=transactions,
    mode="demo",
    broker_name="oanda",
)
```

The normalizer accepts an iterable of broker/OANDA transaction dictionaries or a pandas DataFrame.

## Normalized Columns

The panel keeps the columns compact and dashboard-friendly:

- `transaction_id`
- `timestamp`
- `type`
- `event_class`
- `lifecycle_hint`
- `symbol`
- `instrument`
- `order_id`
- `trade_id`
- `client_order_id`
- `side`
- `units`
- `price`
- `pl`
- `financing`
- `commission`
- `account_balance`
- `reason`
- `description`

`event_class` groups broker transaction types into useful operational buckets:

- `order`
- `fill`
- `close`
- `reduce`
- `cancel`
- `reject`
- `financing`
- `funding`
- `margin`
- `account`
- `unknown`

## OANDA Startup Integration

`run_practice_startup_monitoring` already fetches OANDA account changes through restart sync when a previous transaction checkpoint exists. Those rows are written to:

```text
startup/transactions.csv
```

The same rows are now also passed into the dashboard snapshot as `broker_transactions`, so `dashboard/live.html` and `dashboard/snapshot.json` show a recent broker transaction stream.

## Hosted Monitor

`write_monitoring_snapshot_json` includes:

```json
{
  "broker_transactions": []
}
```

The hosted monitor serves this through `/snapshot.json` and embeds it inside `/status.json`.

## Incident Bundles

When an incident bundle receives a monitoring snapshot, it writes:

```text
monitoring_broker_transactions.csv
```

Use this to review what the broker reported around a crash, restart, rejected order, manual platform action, financing event, or unexpected close.

## Operational Use

Useful reviews:

- Did the broker fill an order while the bot was offline?
- Was a position closed by stop loss, take profit, manual action, or broker-side liquidation?
- Were pending orders cancelled or rejected?
- Did financing, commission, or account-balance events happen after restart?
- Does the lifecycle store match the broker transaction trail?

The stream is not a replacement for broker restart sync or lifecycle recovery. It is a visibility layer that helps the operator understand broker-side truth.
