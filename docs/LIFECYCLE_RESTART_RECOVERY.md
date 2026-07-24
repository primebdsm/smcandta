# Lifecycle Restart Recovery

Lifecycle restart recovery synchronizes persisted trade lifecycle records with broker-open positions after a crash, deploy, VPS reboot, or manual restart.

It should run after broker position-ledger restart sync and before preflight or the repeated bot loop.

## What It Solves

The bot can crash between these steps:

- signal generated
- risk approved
- order submitted
- broker filled
- lifecycle saved as open
- broker position later closed by SL/TP or manually

After restart, the SQLite lifecycle store may not match the broker. This module checks active lifecycle rows against broker positions and either blocks startup or explicitly repairs lifecycle state.

## Main APIs

```python
from smc_ta import LifecycleRecoveryConfig, recover_lifecycle_after_restart

report = recover_lifecycle_after_restart(
    broker,
    lifecycle_store,
    symbol="EURUSD",
    config=LifecycleRecoveryConfig(
        create_missing_lifecycles_for_broker_positions=True,
        mark_missing_broker_positions_closed=True,
        fail_unfilled_lifecycles_without_broker_position=True,
    ),
)

if not report.ok:
    raise RuntimeError(report.summary())
```

`DemoTradingBot` also exposes:

```python
report = bot.recover_lifecycle_after_restart(config=LifecycleRecoveryConfig(...))
```

## Safe Defaults

By default, recovery is conservative:

- matched active lifecycle records are synchronized from broker position truth
- broker positions with no active lifecycle block startup
- open lifecycle records with no broker position block startup
- unfilled active lifecycle records with no broker position block startup
- duplicate active lifecycle rows for the same broker position block startup

These repairs require explicit config flags:

- `create_missing_lifecycles_for_broker_positions`
- `mark_missing_broker_positions_closed`
- `fail_unfilled_lifecycles_without_broker_position`
- `match_unlinked_records_by_symbol_side`

## Recovery Modes

`create_missing_lifecycles_for_broker_positions=True`

Creates an `open` lifecycle record for a broker position that exists but has no active lifecycle. This is useful if the bot opened a trade and crashed before saving the lifecycle fill.

`mark_missing_broker_positions_closed=True`

Marks an active `open` or `partially_closed` lifecycle as `closed` when the broker no longer has the position. This is useful when a broker-side SL/TP, manual close, or liquidation happened while the bot was offline.

`fail_unfilled_lifecycles_without_broker_position=True`

Marks an active `signal`, `approved`, or `submitted` lifecycle as `failed` when no broker position exists after restart.

`match_unlinked_records_by_symbol_side=True`

Allows recovery to match one unlinked lifecycle to exactly one broker position with the same symbol and side. This should be used carefully because it repairs by inference.

## CLI

Paper snapshot demo:

```bash
python examples/lifecycle_restart_recovery.py \
  --broker paper \
  --symbol EURUSD \
  --lifecycle-path trade_lifecycle.sqlite \
  --create-missing-lifecycles \
  --output reports/lifecycle_recovery.json
```

OANDA practice:

```bash
export OANDA_ACCOUNT_ID="..."
export OANDA_TOKEN="..."
export SMC_TA_OANDA_PRACTICE=true

python examples/lifecycle_restart_recovery.py \
  --broker oanda \
  --symbol EURUSD \
  --lifecycle-path oanda_trade_lifecycle.sqlite \
  --create-missing-lifecycles \
  --mark-missing-closed \
  --fail-unfilled \
  --output reports/oanda_lifecycle_recovery.json
```

The command exits with `0` when recovery is safe and `2` when startup should remain blocked.

When it exits with `2`, keep the bot stopped, review broker positions and lifecycle records manually, and capture an incident bundle. See `docs/INCIDENT_PROCEDURES.md`.

## Recommended Startup Order

1. Load runtime config.
2. Build broker adapter.
3. Run broker restart sync against the expected-position ledger.
4. Run lifecycle restart recovery against the lifecycle store.
5. Run preflight readiness.
6. Start the bot loop only when all startup reports are OK.

The full deployment sequence is documented in `docs/DEPLOYMENT_RUNBOOK.md`.

## Profit Impact

This does not create market edge.

It protects the trading process by preventing the bot from continuing with stale lifecycle state. That reduces duplicate order risk, unmanaged open-position risk, and bad post-restart reporting, which can protect capital during demo and live operations.
