# OANDA Execution Validation

This workflow validates the OANDA practice execution path with guarded minimum-size demo trades.

It is the next step after the non-trading readiness check in `docs/OANDA_PRACTICE_HARDENING.md`.

## What It Validates

The validator can run:

- minimum-unit demo market order
- SL/TP demo market order
- close-position test for each opened validation trade
- intentionally rejected invalid-order test
- restart/reconciliation test with SQLite expected-position ledger
- spread/slippage report from actual practice fills

It uses `OandaBroker`, `OandaConfig`, `BrokerReconciler`, and the SQLite position ledger already in the project.

## Safety Defaults

The CLI does not place orders unless `--execute` is passed.

It always uses OANDA practice mode:

```python
OandaConfig(..., practice=True)
```

Execution mode refuses to run if the selected symbol already has open positions unless `--allow-existing-positions` is passed. This protects unrelated demo trades.

## Dry Run

Set credentials:

```bash
export OANDA_ACCOUNT_ID="..."
export OANDA_TOKEN="..."
```

Run:

```bash
python examples/oanda_execution_validate.py --symbol EURUSD --max-spread-pips 2
```

Dry run checks account, instrument metadata, pricing, existing positions, and the planned SL/TP prices. It does not place orders.

## Execute Practice Validation

Use only on an OANDA practice account:

```bash
python examples/oanda_execution_validate.py \
  --symbol EURUSD \
  --side buy \
  --max-spread-pips 2 \
  --max-order-slippage-pips 1 \
  --ledger-path .oanda_execution_validation.sqlite \
  --output-dir reports/oanda_validation \
  --execute
```

The validator will:

1. Open a minimum-size market order.
2. Find the OANDA trade ID created by the fill.
3. Close that exact trade.
4. Open a second minimum-size market order with stop-loss and take-profit on fill.
5. Save the open position to the expected-position ledger.
6. Re-open the SQLite ledger to simulate restart.
7. Reconcile broker positions against the restarted ledger.
8. Close the SL/TP validation trade.
9. Mark the ledger position closed.
10. Reconcile again.
11. Send an intentionally invalid zero-unit order and confirm OANDA rejects it.
12. Print and optionally save a spread/slippage report.

## Python API

```python
from smc_ta.broker import (
    OandaBroker,
    OandaConfig,
    OandaExecutionValidationConfig,
    run_oanda_practice_execution_validation,
)

broker = OandaBroker(OandaConfig(account_id="...", token="...", practice=True))
report = run_oanda_practice_execution_validation(
    broker,
    config=OandaExecutionValidationConfig(
        symbol="EURUSD",
        ledger_path=".oanda_execution_validation.sqlite",
    ),
    execute=True,
)

print(report.summary())
print(report.to_frame())
print(report.execution_frame())
```

## Report Outputs

`report.to_frame()` contains readiness and execution checks.

`report.execution_frame()` contains:

- validation label
- side
- units
- reference price
- fill price
- spread
- spread in pips
- slippage
- slippage in pips
- commission
- OANDA fill/order ID
- OANDA position/trade ID

## How This Helps

This validation does not improve strategy edge by itself. It improves execution confidence.

It helps answer:

- Can the account trade this instrument?
- Does minimum-size order placement work?
- Does SL/TP-on-fill work?
- Can the bot identify the broker trade ID?
- Can the bot close the exact validation trade?
- Does restart/reconciliation still match broker state?
- Are live practice spreads and slippage close to backtest assumptions?
- Are rejected orders handled without crashing the bot loop?

## What Still Remains

After this validation passes, still collect at least several weeks of demo-forward results before live-money trading.

Next production steps:

- live monitoring dashboard
- run broker restart sync on every OANDA bot process start
- process supervisor and log rotation
- incident/kill-switch runbook
- demo-forward reporting package
