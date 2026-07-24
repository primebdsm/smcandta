# Process Supervision And Log Rotation

This repository includes deployment helpers for running a paper/demo/live bot process under a real supervisor.

The helpers generate reviewable artifacts. They do not install services, start processes, restart bots, or touch broker state.

## Main APIs

```python
from smc_ta import (
    LogrotateConfig,
    RuntimeLogConfig,
    SupervisorConfig,
    configure_runtime_logging,
    write_supervisor_artifacts,
)
```

## Generate Supervisor Artifacts

```bash
python examples/generate_ops_artifacts.py \
  --output-dir deployment/supervisor \
  --service-name smc-ta-demo \
  --command "python examples/demo_paper_loop.py" \
  --env-file .env.demo \
  --log-dir logs
```

This writes:

- `smc-ta-demo.service` for Linux systemd
- `smc-ta-demo.plist` for macOS launchd
- `smc-ta-demo.logrotate` for external log rotation
- `README.md` with install commands to review

Review the generated paths and command before installing anything into the operating system.

## systemd Pattern

The generated unit uses:

- `Restart=always`
- `RestartSec=10`
- `StartLimitIntervalSec=300`
- `StartLimitBurst=3`
- `KillSignal=SIGINT`
- separate stdout/stderr log files
- optional `EnvironmentFile`

The supervisor restarts the process. It must not bypass the bot's startup sequence. The bot entrypoint should still run:

1. secret resolution
2. runtime config validation
3. broker restart sync
4. lifecycle restart recovery
5. preflight readiness
6. dashboard refresh
7. monitoring snapshot refresh
8. bot loop only when reports are OK

## launchd Pattern

The generated plist uses:

- `RunAtLoad`
- `KeepAlive`
- `ProgramArguments`
- `WorkingDirectory`
- stdout/stderr paths
- `PYTHONUNBUFFERED=1`

Use it for local macOS demo or practice processes. For production Linux VPS use systemd or another managed supervisor.

## Python Runtime Logs

Inside the bot process:

```python
from smc_ta import RuntimeLogConfig, configure_runtime_logging

logger = configure_runtime_logging(
    RuntimeLogConfig(
        log_dir="logs",
        logger_name="smc_ta.live",
        file_name="bot.log",
        max_bytes=10_000_000,
        backup_count=10,
        json_lines=True,
    )
)

logger.info("bot_cycle_complete", extra={"symbol": "EURUSD", "action": "blocked"})
```

This writes rotating application logs through Python's `RotatingFileHandler`.

## External Log Rotation

The supervisor also writes stdout/stderr logs. Use the generated logrotate file for those logs:

```python
from smc_ta import LogrotateConfig, write_logrotate_config

write_logrotate_config(
    LogrotateConfig(log_glob="logs/*.log", rotate_count=14),
    "deployment/supervisor/smc-ta-demo.logrotate",
)
```

Log retention policy should match account risk and audit needs. Keep enough logs to investigate restarts, news blocks, spread spikes, rejected orders, and emergency stops.

## Operational Rule

A supervisor should restart a crashed process, but a restarted process must still block itself when startup reports are unsafe.

Do not configure the supervisor to delete state, reset emergency stop, overwrite SQLite files, or auto-close broker positions. Those actions belong in incident procedures with manual review.

## Incident Link

If the service enters a crash loop, logs stop updating, or the dashboard becomes stale, treat it as an incident and follow `docs/INCIDENT_PROCEDURES.md`.

If hosted monitoring is running as a separate service, generate and review a separate supervisor unit for `examples/serve_monitoring.py`. Keep it bound to localhost unless HTTPS, VPN, or tunnel controls are in front of it. See `docs/HOSTED_MONITORING.md`.
