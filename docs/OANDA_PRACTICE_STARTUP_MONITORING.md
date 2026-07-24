# OANDA Practice Startup Monitoring

This workflow runs the startup controls together and writes one artifact bundle for review before a repeated demo/live bot loop starts.

It is a monitoring and readiness drill. It does not place orders. Use `docs/OANDA_EXECUTION_VALIDATION.md` for the separate minimum-size practice order test.

## What It Runs

`run_practice_startup_monitoring` combines:

- redacted OANDA secret resolution
- OANDA practice readiness checks
- candle download or CSV candle loading
- broker restart sync with transaction checkpoint and pending-order reports
- broker-synchronized lifecycle recovery
- preflight readiness
- broker connectivity probe
- alert delivery probe
- SMC/TA analysis snapshot
- local live dashboard HTML
- summary JSON
- incident bundle when the drill blocks after a dashboard snapshot is available

Paper mode is available for local smoke tests without broker credentials.

## Paper Smoke Test

```bash
python examples/oanda_practice_startup_monitor.py \
  --broker paper \
  --symbol EURUSD \
  --timeframe M15 \
  --output-dir reports/practice_startup/paper
```

Expected result:

```text
practice_startup_monitoring_ok
```

## OANDA Practice Drill

Set credentials through the environment or an `.env` file:

```bash
export OANDA_ACCOUNT_ID="..."
export OANDA_TOKEN="..."
```

Then run:

```bash
python examples/oanda_practice_startup_monitor.py \
  --broker oanda \
  --symbol EURUSD \
  --timeframe M15 \
  --max-spread-pips 2 \
  --output-dir reports/practice_startup/oanda \
  --adopt-unmanaged \
  --mark-missing-positions-closed \
  --create-missing-lifecycles \
  --mark-missing-lifecycles-closed \
  --fail-unfilled-lifecycles
```

Use repair flags only after the operator has reviewed broker-side exposure. Without repair flags, restart sync and lifecycle recovery stay report-only and block on mismatches.

## Artifact Bundle

The output directory can include:

- `summary.json`: redaction-safe status and artifact map
- `startup/secrets.json`: redacted credential-resolution report
- `startup/candles.csv`: normalized candles used by the analyzer
- `startup/oanda_readiness.csv`: account, instrument, price, spread, and freshness checks
- `startup/restart_sync.json`: broker-vs-ledger startup state
- `startup/restart_sync_actions.csv`: repair actions or blocking findings
- `startup/pending_orders.csv`: broker pending orders
- `startup/transactions.csv`: OANDA transaction rows since the last checkpoint
- `startup/lifecycle_recovery.json`: lifecycle-vs-broker recovery report
- `startup/lifecycle_records.csv`: recovered lifecycle state
- `startup/preflight.csv`: final startup gate checks
- `startup/broker_connectivity.csv`: read-only account and position probe
- `startup/alert_delivery.csv`: alert probe status
- `dashboard/live.html`: local monitoring dashboard
- `dashboard/snapshot.json`: machine-readable monitor snapshot
- `incident/`: incident evidence when a post-snapshot startup gate blocks
- `state/`: SQLite ledger, checkpoint, and lifecycle state when default paths are used

The default CLI probes only an in-memory alert channel. In a bot integration, pass real alert channels into `run_practice_startup_monitoring(..., alert_channels=(("telegram", channel),))`.

## Python Integration

```python
from smc_ta import PracticeStartupRunConfig, run_practice_startup_monitoring

result = run_practice_startup_monitoring(
    PracticeStartupRunConfig(
        broker="oanda",
        symbol="EURUSD",
        timeframe="M15",
        output_dir="reports/practice_startup/oanda",
        max_spread_pips=2,
        create_missing_lifecycles=True,
    )
)

if not result.ok:
    raise RuntimeError(result.summary())
```

Use the same configured broker, ledger, checkpoint store, lifecycle database, and dashboard paths that the live/demo bot will use. That makes the startup report a real rehearsal for the next process start.
