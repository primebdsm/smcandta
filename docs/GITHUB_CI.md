# GitHub CI

The repository includes a GitHub Actions workflow at `.github/workflows/ci.yml`.

It is designed as a safe repository gate for pull requests and pushes to `main`. It does not use broker credentials, place orders, call OANDA, send alerts, or require live market access.

## What It Runs

The `tests` job runs on Ubuntu with Python 3.10, 3.11, and 3.12:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

It also performs a public API smoke import for the core bot-integration exports, including:

- `analyze_forex`
- `run_backtest`
- `run_demo_forward_test`
- `run_risk_stress_test`
- `sync_broker_state_after_restart`
- `TransactionReconciliationEvent`
- `Mt5Config`
- `build_mt5_config`

The `package` job builds the source distribution and wheel:

```bash
python -m pip install --upgrade pip build
python -m build
python -m pip install dist/*.whl
```

Then it imports `smc_ta` from the installed wheel.

## Triggers

The workflow runs on:

- push to `main`
- pull request into `main`
- manual `workflow_dispatch`

## Why It Matters

CI is not a trading instrument and does not prove profitability.

It protects the project by preventing broken code, missing exports, packaging failures, and test regressions from entering the GitHub `main` branch. For a Forex bot, this matters because runtime safety tools only help if the package can install, import, and run the verified test suite consistently.

## Local Equivalent

Run the main local check before pushing:

```bash
.venv/bin/python -m pytest
```

For package build parity with CI:

```bash
.venv/bin/python -m pip install build
.venv/bin/python -m build --outdir /tmp/smc_ta_dist
```
