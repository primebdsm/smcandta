# Broker Restart Sync

This module helps the bot recover broker-side state after a crash, deploy, reboot, or manual restart.

It is designed for the startup phase before the bot resumes signal generation or order placement.

## What It Solves

After a restart, the Python process may have lost in-memory state while the broker still has:

- open positions
- protective stop-loss/take-profit orders
- pending entry orders
- transactions created while the bot was offline

The restart sync workflow compares the broker against the local expected-position ledger, fetches broker transaction checkpoints when the adapter supports it, reports pending orders, and optionally repairs the ledger.

## Main APIs

```python
from smc_ta.reconciliation import (
    RestartSyncConfig,
    SQLitePositionLedger,
    SQLiteSyncCheckpointStore,
    sync_broker_state_after_restart,
)

ledger = SQLitePositionLedger("positions.sqlite")
checkpoints = SQLiteSyncCheckpointStore("positions.sqlite")

report = sync_broker_state_after_restart(
    broker,
    ledger,
    symbol="EURUSD",
    checkpoint_store=checkpoints,
    config=RestartSyncConfig(
        adopt_unmanaged_broker_positions=True,
        mark_missing_expected_positions_closed=True,
        update_mismatched_expected_positions=True,
    ),
)

if not report.ok:
    raise RuntimeError(report.summary())
```

`DemoTradingBot` also exposes:

```python
report = bot.sync_after_restart(checkpoint_store=checkpoints)
```

## Safe Defaults

By default the workflow is report-only.

It will not mutate the ledger unless these flags are explicitly enabled:

- `adopt_unmanaged_broker_positions`
- `mark_missing_expected_positions_closed`
- `update_mismatched_expected_positions`

This is intentional. Startup recovery should first show exactly what changed, then only repair local state when the operator or deployment config allows it.

## Recovery Modes

`adopt_unmanaged_broker_positions=True`

Records broker-open positions into the expected-position ledger. This is useful when the bot opened a trade, crashed before persisting it, and the broker still has the position.

`mark_missing_expected_positions_closed=True`

Marks ledger positions as closed when they no longer exist at the broker. This is useful when a broker stop-loss/take-profit or manual close happened while the bot was offline.

`update_mismatched_expected_positions=True`

Updates the expected ledger from broker truth when units, side, symbol, or entry price differ.

`block_on_unlinked_pending_orders=True`

Blocks startup when the broker has a pending order that is not linked to a synced broker position. Protective orders linked by broker trade ID are reported as safe; independent pending orders should be reviewed before the bot trades again.

## OANDA Support

`OandaBroker` now supports restart sync with:

- `get_latest_transaction_id()`
- `get_account_changes(since_transaction_id)`
- `get_pending_orders(symbol=None)`

The account changes endpoint is used to poll account orders, trades, positions, transactions, and the next `lastTransactionID` checkpoint. OANDA documents this at:

https://developer.oanda.com/rest-live-v20/account-ep/

Pending orders are read from OANDA's pending-order endpoint:

https://developer.oanda.com/rest-live-v20/order-ep/

The generic sync service still works with brokers that only implement `get_open_positions()`. Those brokers get position recovery without transaction checkpoints or pending-order snapshots.

## CLI

Paper demo:

```bash
python examples/broker_restart_sync.py \
  --broker paper \
  --symbol EURUSD \
  --ledger-path restart_sync_positions.sqlite \
  --adopt-unmanaged
```

OANDA practice:

```bash
export OANDA_ACCOUNT_ID="..."
export OANDA_TOKEN="..."
export SMC_TA_OANDA_PRACTICE=true

python examples/broker_restart_sync.py \
  --broker oanda \
  --symbol EURUSD \
  --ledger-path oanda_positions.sqlite \
  --adopt-unmanaged \
  --mark-missing-closed \
  --output reports/restart_sync.json
```

The command exits with `0` when restart sync is safe and `2` when startup should remain blocked.

## Live Startup Order

Recommended production startup:

1. Load runtime config and credentials.
2. Build broker adapter.
3. Build SQLite expected-position ledger.
4. Build SQLite transaction checkpoint store.
5. Run restart sync.
6. Run lifecycle restart recovery.
7. Run preflight readiness.
8. Start live/demo bot loop only when all startup reports are OK.

Lifecycle recovery is documented in `docs/LIFECYCLE_RESTART_RECOVERY.md`.

## Profit Impact

This does not create trading edge by itself.

It can protect profitability by preventing duplicate entries, unmanaged exposure, stale local position state, and unknown pending orders after a crash. That reduces avoidable losses from operational mistakes, especially around news, spread spikes, VPS restarts, and broker-side SL/TP events.
