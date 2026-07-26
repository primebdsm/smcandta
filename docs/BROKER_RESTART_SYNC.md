# Broker Restart Sync

This module helps the bot recover broker-side state after a crash, deploy, reboot, or manual restart.

It is designed for the startup phase before the bot resumes signal generation or order placement.

## What It Solves

After a restart, the Python process may have lost in-memory state while the broker still has:

- open positions
- protective stop-loss/take-profit orders
- pending entry orders
- transactions created while the bot was offline

The restart sync workflow compares the broker against the local expected-position ledger, fetches broker transaction checkpoints when the adapter supports it, reconciles OANDA transaction events, reports pending orders, and optionally repairs the ledger.

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

transaction_events = report.transaction_events_frame()
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

Transaction reconciliation is enabled by default through `reconcile_broker_transactions=True`, but it is evidence-first. It classifies OANDA transaction history and can block on rejected transactions, but it does not repair the expected-position ledger unless one of the explicit repair flags above is enabled.

## Recovery Modes

`adopt_unmanaged_broker_positions=True`

Records broker-open positions into the expected-position ledger. This is useful when the bot opened a trade, crashed before persisting it, and the broker still has the position.

`mark_missing_expected_positions_closed=True`

Marks ledger positions as closed when they no longer exist at the broker. This is useful when a broker stop-loss/take-profit or manual close happened while the bot was offline.

`update_mismatched_expected_positions=True`

Updates the expected ledger from broker truth when units, side, symbol, or entry price differ.

`block_on_unlinked_pending_orders=True`

Blocks startup when the broker has a pending order that is not linked to a synced broker position. Protective orders linked by broker trade ID are reported as safe; independent pending orders should be reviewed before the bot trades again.

`block_on_rejected_transactions=True`

Blocks startup when OANDA reports rejected order transactions since the previous checkpoint. Use `block_on_rejected_transactions=False` only when rejected-order events are expected and separately reviewed.

`block_on_cancelled_transactions=False`

Reports cancelled OANDA orders as warnings by default. Set it to `True` when any broker-side order cancellation should stop startup until reviewed.

## OANDA Support

`OandaBroker` now supports restart sync with:

- `get_latest_transaction_id()`
- `get_account_changes(since_transaction_id)`
- `get_transactions_since(since_transaction_id)`
- `get_pending_orders(symbol=None)`

The account changes endpoint is used to poll account orders, trades, positions, transactions, and the next `lastTransactionID` checkpoint. The transaction-history endpoint is used to fetch the direct transaction trail since the previous checkpoint when available. OANDA documents these endpoints at:

https://developer.oanda.com/rest-live-v20/account-ep/

https://developer.oanda.com/rest-live-v20/transaction-ep/

Pending orders are read from OANDA's pending-order endpoint:

https://developer.oanda.com/rest-live-v20/order-ep/

## Transaction Reconciliation Events

When OANDA transactions are available, restart sync normalizes important events into `report.transaction_events_frame()` and the JSON report's `transaction_events` array.

Current classifications include:

- `oanda_trade_open_confirmed`
- `oanda_trade_open_missing_from_ledger`
- `oanda_trade_closed`
- `oanda_trade_reduced`
- `oanda_order_rejected`
- `oanda_order_cancelled`
- `oanda_financing_transaction`
- `oanda_funding_transaction`
- `oanda_margin_transaction`

When `mark_missing_expected_positions_closed=True`, an OANDA close transaction can provide the ledger close timestamp and exit price. When `adopt_unmanaged_broker_positions=True` or `update_mismatched_expected_positions=True`, the repaired ledger metadata includes the related OANDA transaction ID, order ID, reason, PnL, commission, and account balance when OANDA supplied them.

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

Use `--allow-rejected-transactions` only when rejected OANDA transactions are expected and separately reviewed. Use `--block-cancelled-transactions` when any broker-side cancellation should block startup instead of warning.

The command exits with `0` when restart sync is safe and `2` when startup should remain blocked.

When it exits with `2`, keep the bot stopped, review broker positions and pending orders manually, and capture an incident bundle. See `docs/INCIDENT_PROCEDURES.md`.

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

The full deployment sequence is documented in `docs/DEPLOYMENT_RUNBOOK.md`.

## Profit Impact

This does not create trading edge by itself.

It can protect profitability by preventing duplicate entries, unmanaged exposure, stale local position state, and unknown pending orders after a crash. That reduces avoidable losses from operational mistakes, especially around news, spread spikes, VPS restarts, and broker-side SL/TP events.
