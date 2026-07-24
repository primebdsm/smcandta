# Incident Procedures

Use this playbook when the bot, broker, dashboard, reconciliation, lifecycle recovery, or preflight reports an unsafe state.

The first goal is capital protection. The second goal is accurate evidence. The third goal is controlled recovery.

## First 60 Seconds

1. Activate the emergency stop or create the configured manual stop file.
2. Stop new bot cycles.
3. Check the broker platform manually for open positions and pending orders.
4. Do not restart, redeploy, reset, or delete state files yet.
5. Capture an incident bundle.
6. Decide whether broker-side positions must be reduced or closed manually.

The repository can help document and reconcile state, but broker truth wins during an incident.

## Severity Levels

| Severity | Meaning | Required Response |
| --- | --- | --- |
| SEV1 | Live-money exposure, unexpected position, runaway orders, margin risk, or large drawdown | Stop bot, inspect broker manually, preserve evidence, resolve exposure before restart |
| SEV2 | Demo/practice execution issue, restart sync blocked, lifecycle recovery blocked, or OANDA practice validation failed | Stop bot, preserve evidence, repair state only after review |
| SEV3 | Data, news, dashboard, journal, alert, broker connectivity, or monitoring degradation while trading is blocked or unaffected | Keep trading blocked if visibility is incomplete |
| SEV4 | Documentation, report formatting, or non-runtime issue | Fix normally after confirming no execution impact |

## Evidence Bundle

Create a bundle for every SEV1 or SEV2 incident:

```python
from smc_ta import write_incident_report_bundle

bundle = write_incident_report_bundle(
    "reports/incidents/incident-001",
    title="restart sync blocked by unlinked pending order",
    severity="SEV2",
    symbol="EURUSD",
    runtime_config=runtime,
    preflight_report=preflight,
    restart_sync_report=restart_sync,
    lifecycle_recovery_report=lifecycle_recovery,
    monitoring_snapshot=snapshot,
    emergency_stop_result=emergency_stop_result,
    notes=("bot stayed blocked", "broker platform checked manually"),
)
```

The bundle writes:

- `incident_summary.json`
- `incident_report.md`
- report CSV files when available
- open-position CSV when positions are supplied
- monitoring and journal CSV files when supplied

## Emergency Stop Triggered

Symptoms:

- preflight reports `emergency_stop_active`
- dashboard status is blocking
- bot blocks new orders
- optional close-all behavior may have been requested

Procedure:

1. Leave the stop latched.
2. Check broker positions manually.
3. Confirm whether positions were closed by the bot or still exist.
4. Save an incident bundle.
5. Identify the trigger reason: manual stop, equity, daily loss, drawdown, max positions, reconciliation failure, or runtime errors.
6. Fix the root cause.
7. Run broker restart sync.
8. Run lifecycle restart recovery.
9. Run preflight.
10. Reset the emergency stop only after every report is safe.

## Broker Restart Sync Blocked

Common causes:

- unmanaged broker position
- expected ledger position missing at broker
- units, side, or entry mismatch
- unlinked pending order
- transaction checkpoint fetch failed

Procedure:

1. Keep trading stopped.
2. Inspect broker platform open positions and pending orders.
3. Save restart sync JSON and an incident bundle.
4. If a broker position is valid, rerun sync with `adopt_unmanaged_broker_positions=True`.
5. If a ledger position was closed broker-side, rerun with `mark_missing_expected_positions_closed=True`.
6. If a mismatch reflects broker truth, rerun with `update_mismatched_expected_positions=True`.
7. Cancel or document unlinked pending orders before resuming.
8. Continue to lifecycle recovery and preflight only after restart sync is OK.

## Lifecycle Recovery Blocked

Common causes:

- broker position has no active lifecycle record
- active lifecycle record has no broker position
- submitted lifecycle never became a broker position
- duplicate active lifecycle rows point to the same broker position

Procedure:

1. Keep trading stopped.
2. Inspect the broker position and lifecycle database.
3. Save lifecycle recovery JSON and an incident bundle.
4. If broker position is valid, rerun with `create_missing_lifecycles_for_broker_positions=True`.
5. If broker position is gone, rerun with `mark_missing_broker_positions_closed=True`.
6. If submitted trade is stale and not filled, rerun with `fail_unfilled_lifecycles_without_broker_position=True`.
7. Resolve duplicate lifecycle rows manually or by choosing the correct surviving record.
8. Run preflight only after lifecycle recovery is OK.

## Unexpected Broker Position

Treat unexpected live exposure as SEV1.

Procedure:

1. Stop the bot immediately.
2. Check whether the position has SL/TP protection.
3. Decide manually whether to close, reduce, or keep the position.
4. Capture broker screenshots or broker export outside the repo if needed.
5. Run restart sync report-only first.
6. Adopt into local state only if the position is intentionally kept.
7. Create or recover lifecycle state only after broker-side exposure is understood.

## Spread Or Slippage Spike

Symptoms:

- OANDA practice validation reports large spread or slippage
- dashboard execution samples show abnormal execution
- orders are rejected by broker price bounds

Procedure:

1. Keep or activate trade block.
2. Check economic calendar and market session.
3. Increase observation window before placing new orders.
4. Review spread/slippage report against backtest assumptions.
5. Tighten `oanda_max_spread_pips` or max slippage config if needed.
6. Resume only after spread is back inside policy and preflight is safe.

## Data Quality Or News Filter Failure

Symptoms:

- missing candles
- invalid OHLC relationship
- duplicate timestamps
- stale economic calendar
- required news filter missing

Procedure:

1. Keep trading blocked.
2. Redownload or replace the candle sample.
3. Validate data with `examples/validate_data.py`.
4. Refresh the economic calendar provider.
5. Verify UTC timestamps and broker/server timezone assumptions.
6. Rerun preflight.

## Dashboard Or Monitoring Stale

Symptoms:

- dashboard timestamp is old
- journal is not updating
- lifecycle records do not match broker state
- alerts are not delivered
- Broker Connectivity panel is blocking or stale
- Alert Delivery panel shows warning/blocking

Procedure:

1. Do not rely on the dashboard for exposure.
2. Check broker platform manually.
3. Save an incident bundle if trading was active.
4. Verify journal and lifecycle paths are writable.
5. Rerun read-only broker connectivity checks.
6. Rerun explicit alert delivery probes if alert channels are required.
7. Regenerate the dashboard and snapshot.
8. Keep trading blocked until monitoring reflects current broker state.

## Return To Trading Checklist

Before resuming:

- broker platform checked manually
- all unexpected positions and pending orders resolved
- restart sync OK
- lifecycle recovery OK
- preflight OK
- emergency stop inactive
- dashboard current
- journal writes verified
- news filter current when required
- incident bundle saved for SEV1/SEV2
- root cause and fix recorded

If any item is uncertain, stay stopped.
