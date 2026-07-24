# Deployment Runbook

This runbook describes how to deploy the Forex bot stack from this repository into paper, demo, or live operation.

It is an operations document, not a trading-edge document. Deployment should only happen after backtests, walk-forward tests, demo-forward reports, broker practice validation, restart sync, lifecycle recovery, preflight, and monitoring are all working for the selected broker.

## Deployment Rule

Never treat a successful code deploy as permission to trade live money.

Live trading stays blocked unless:

- the runtime config explicitly arms live mode
- the selected broker adapter is configured for live, not practice
- restart sync is safe
- lifecycle recovery is safe
- preflight is safe
- emergency stop is installed and inactive
- monitoring is visible
- the operator has manually checked broker exposure

## Required Persistent State

Keep these files on persistent storage, not a temporary folder:

- expected-position ledger SQLite database
- broker transaction checkpoint database
- trade lifecycle SQLite database
- SQLite or CSV journal
- demo-forward report directory
- incident report directory
- dashboard output path
- manual emergency-stop file path

Back up SQLite files before deploys that change execution, reconciliation, lifecycle, or broker adapter behavior.

## Environment Checklist

Set runtime variables before starting a demo/live process:

```bash
export SMC_TA_MODE=demo
export SMC_TA_BROKER=oanda
export SMC_TA_SYMBOLS=EURUSD
export SMC_TA_TIMEFRAMES=M15
export SMC_TA_MAX_TRADE_RISK_PERCENT=1.0
export SMC_TA_REQUIRE_NEWS_FILTER=true
export SMC_TA_JOURNAL_PATH=state/trades.sqlite
export SMC_TA_LIFECYCLE_DB_PATH=state/trade_lifecycle.sqlite
export SMC_TA_OANDA_ACCOUNT_ID="..."
export SMC_TA_OANDA_TOKEN="..."
export SMC_TA_OANDA_PRACTICE=true
```

For live mode, `SMC_TA_ALLOW_LIVE_TRADING=true` and `SMC_TA_LIVE_CONFIRMATION=I_UNDERSTAND_LIVE_FOREX_RISK` are required. Keep `SMC_TA_OANDA_PRACTICE=true` for practice accounts.

## Pre-Deploy Gate

Run these checks before replacing a running process:

```bash
python -m pytest
python examples/check_runtime_config.py --env-file .env.demo
python examples/validate_data.py --csv EURUSD_M15.csv --symbol EURUSD
python examples/demo_forward_report.py --csv EURUSD_M15.csv --output-dir reports/demo_forward/latest
```

For OANDA practice:

```bash
python examples/oanda_practice_check.py --symbols EURUSD --max-spread-pips 2
python examples/oanda_execution_validate.py --symbol EURUSD --max-spread-pips 2 --execute
```

Only continue when the output is safe and the practice account has no unexpected exposure.

## Startup Sequence

Use this order after a deploy, VPS restart, process crash, or manual restart:

1. Stop the old bot loop cleanly.
2. Verify the broker account manually in the broker platform.
3. Back up the SQLite state files.
4. Load runtime config and credentials.
5. Build broker, ledger, checkpoint, journal, lifecycle, news, risk, and emergency-stop objects.
6. Run broker restart sync.
7. Run lifecycle restart recovery.
8. Run preflight readiness.
9. Render or refresh the live dashboard.
10. Start the bot loop only if every startup report is OK.
11. Watch the first cycles and broker platform together.

Command shape:

```bash
python examples/broker_restart_sync.py \
  --broker oanda \
  --symbol EURUSD \
  --ledger-path state/positions.sqlite \
  --adopt-unmanaged \
  --mark-missing-closed \
  --output reports/startup/restart_sync.json

python examples/lifecycle_restart_recovery.py \
  --broker oanda \
  --symbol EURUSD \
  --lifecycle-path state/trade_lifecycle.sqlite \
  --create-missing-lifecycles \
  --mark-missing-closed \
  --fail-unfilled \
  --output reports/startup/lifecycle_recovery.json

python examples/run_preflight.py --env-file .env.demo --csv EURUSD_M15.csv
python examples/live_dashboard_monitor.py --output reports/dashboard/live.html
```

## Live Promotion Gates

Move through these stages in order:

1. Paper replay: deterministic demo-forward reports are acceptable.
2. Demo broker observation: no real orders, only connectivity and data checks.
3. Demo minimum-size execution: OANDA practice execution validation passes.
4. Demo forward loop: repeated closed-candle cycles with reports and dashboard.
5. Tiny live pilot: one symbol, minimum practical size, strict risk and emergency stop.
6. Controlled expansion: add symbols only after journal and incident review.

Do not promote after a single good day. Require enough trades to compare setup, session, spread, and slippage behavior against the backtest/demo-forward assumptions.

## Rollback

If deployment is unsafe:

1. Activate emergency stop.
2. Stop the bot process.
3. Check broker positions and pending orders manually.
4. Save an incident bundle.
5. Restore the previous code version if the issue came from the deploy.
6. Restore SQLite state only after comparing it with broker truth.
7. Run broker restart sync, lifecycle recovery, and preflight again.

Never restore an old ledger or lifecycle database over real broker truth without reviewing open positions and pending orders first.

## Post-Deploy Monitoring

The operator should watch:

- dashboard status
- account equity and free margin
- open positions
- pending orders
- emergency-stop status
- preflight warnings
- lifecycle active records
- blocked trade reasons
- spread and slippage samples
- journal writes

For any blocking condition, follow `docs/INCIDENT_PROCEDURES.md`.

## Incident Evidence

Use the incident bundle helper whenever deployment, startup, or runtime becomes unsafe:

```bash
python examples/incident_bundle.py --output-dir reports/incidents/sample --severity SEV2
```

In a real bot process, call `write_incident_report_bundle` with the actual preflight, restart sync, lifecycle recovery, emergency stop, monitoring snapshot, account, positions, and journal events.
