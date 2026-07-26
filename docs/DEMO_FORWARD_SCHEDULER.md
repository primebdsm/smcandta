# Demo-Forward Scheduler

The demo-forward scheduler turns one-off demo-forward report generation into a repeatable operator routine.

It runs `run_demo_forward_test`, writes the normal report bundle, and records scheduler-level evidence:

- `runs/<run_id>/summary.json`
- `runs/<run_id>/report.html`
- `runs/<run_id>/*.csv`
- `history.csv`
- `schedule_summary.json`

This is a process-local scheduler. Use systemd, launchd, tmux, or another supervisor to keep it running on a demo host.

## One-Shot Smoke Run

```bash
python examples/demo_forward_scheduler.py \
  EURUSD_M15.csv \
  --symbol EURUSD \
  --runs 1 \
  --interval-seconds 0 \
  --output-dir reports/demo_forward_scheduler/latest
```

If no CSV is supplied, the CLI uses a deterministic sample candle set for local smoke testing.

## Bounded Schedule

Run four closed-candle report cycles:

```bash
python examples/demo_forward_scheduler.py \
  EURUSD_M15.csv \
  --symbol EURUSD \
  --interval-seconds 900 \
  --runs 4 \
  --output-dir reports/demo_forward_scheduler/eurusd_m15
```

The default behavior skips duplicate candle windows. If the CSV has not received a new final candle since the last successful report, the run is recorded as `skipped:no_new_candle` instead of producing another identical bundle.

On restart, the scheduler reads existing `history.csv` and uses the last successful or warning run's final candle timestamp for the same duplicate-candle check.

## Analytics Dashboard

After a scheduler cycle, render a performance analytics page:

```bash
python examples/performance_analytics_dashboard.py \
  reports/demo_forward_scheduler/eurusd_m15 \
  --output reports/performance_analytics/eurusd_m15.html
```

See `docs/PERFORMANCE_ANALYTICS_DASHBOARD.md`.

## Continuous Loop

```bash
python examples/demo_forward_scheduler.py \
  EURUSD_M15.csv \
  --symbol EURUSD \
  --interval-seconds 900 \
  --loop \
  --stop-on-failure \
  --output-dir reports/demo_forward_scheduler/eurusd_m15
```

Use `--loop` only under an external supervisor. The process stops on `Ctrl-C`, process manager shutdown, or the first failed run when `--stop-on-failure` is enabled.

## Python API

```python
from smc_ta import DemoForwardConfig, DemoForwardScheduleConfig, run_demo_forward_schedule
from smc_ta.risk import RiskConfig

result = run_demo_forward_schedule(
    DemoForwardScheduleConfig(
        csv_path="EURUSD_M15.csv",
        output_dir="reports/demo_forward_scheduler/eurusd_m15",
        interval_seconds=900,
        max_runs=4,
        demo_forward=DemoForwardConfig(
            symbol="EURUSD",
            warmup_candles=150,
            max_cycles=500,
            risk=RiskConfig(risk_percent_per_trade=0.5, max_units=10_000),
        ),
    )
)

if not result.ok:
    raise RuntimeError(result.summary())
```

Bot integrations can pass a `candles_loader` callable instead of `csv_path`. That loader can download fresh broker candles, load a database window, or read a CSV updated by another process.

## How It Works

1. Load candles from the configured CSV or custom loader.
2. Normalize candles and read the final candle timestamp.
3. Skip the run when the final candle matches the last successful scheduled run.
4. Run the existing demo-forward bot-path replay.
5. Write a timestamped report bundle under `runs/`.
6. Append the outcome to `history.csv`.
7. Rewrite `schedule_summary.json` after every run.
8. Sleep until the next run unless the configured run count is complete.

## Operational Use

Use this after backtests and walk-forward optimization, and before a broker-demo loop. The scheduler helps collect repeated evidence about setup behavior, blocked reasons, session performance, fills, drawdown, and trade lifecycle behavior.

It does not predict profit. It can improve live-readiness indirectly by making weak sessions, fragile setups, excessive blocking, or unhealthy equity behavior visible before real execution is promoted.
