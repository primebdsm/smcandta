from __future__ import annotations

import json
import sys

from smc_ta import (
    CommandSecretSource,
    EnvSecretSource,
    JsonSecretSource,
    LogrotateConfig,
    RuntimeLogConfig,
    SecretResolutionConfig,
    SupervisorConfig,
    configure_runtime_logging,
    resolve_runtime_secrets,
    write_secret_resolution_report,
    write_supervisor_artifacts,
)


def test_secret_resolution_redacts_and_uses_command_provider(tmp_path) -> None:
    json_path = tmp_path / "secrets.json"
    json_path.write_text(json.dumps({"OANDA_TOKEN": "json-secret-token"}), encoding="utf-8")
    command = (
        sys.executable,
        "-c",
        "import json; print(json.dumps({'OANDA_TOKEN': 'command-secret-token'}))",
    )

    report = resolve_runtime_secrets(
        SecretResolutionConfig(
            sources=(
                EnvSecretSource(keys=("OANDA_ACCOUNT_ID",)),
                JsonSecretSource(json_path),
                CommandSecretSource(command),
            ),
            required_keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN"),
        ),
        env={"OANDA_ACCOUNT_ID": "practice-account-id"},
    )
    output = write_secret_resolution_report(report, tmp_path / "secret_report.json")
    text = output.read_text(encoding="utf-8")

    assert report.ok
    assert report.values["OANDA_TOKEN"] == "command-secret-token"
    assert report.used_sources["OANDA_TOKEN"] == "command"
    assert "command-secret-token" not in text
    assert "practice-account-id" not in text
    assert report.safe_values()["OANDA_TOKEN"].endswith("oken")


def test_secret_resolution_blocks_missing_required_secret() -> None:
    report = resolve_runtime_secrets(
        SecretResolutionConfig(
            sources=(EnvSecretSource(keys=("OANDA_ACCOUNT_ID",)),),
            required_keys=("OANDA_ACCOUNT_ID", "OANDA_TOKEN"),
        ),
        env={"OANDA_ACCOUNT_ID": "practice-account-id"},
    )

    assert not report.ok
    assert "missing_required_secret" in report.blocking_reasons
    assert report.missing_keys == ("OANDA_TOKEN",)


def test_supervisor_artifacts_include_service_and_rotation_files(tmp_path) -> None:
    env_file = tmp_path / ".env.demo"
    env_file.write_text("SMC_TA_MODE=demo\n", encoding="utf-8")
    config = SupervisorConfig(
        service_name="smc-ta-test",
        description="SMC TA test bot",
        command=("python", "examples/demo_paper_loop.py"),
        working_directory=tmp_path,
        env_file=env_file,
        log_dir=tmp_path / "logs",
    )

    bundle = write_supervisor_artifacts(
        config,
        tmp_path / "deployment",
        logrotate=LogrotateConfig(log_glob=tmp_path / "logs" / "*.log", rotate_count=7),
    )

    systemd = bundle.systemd_unit.read_text(encoding="utf-8")
    plist = bundle.launchd_plist.read_text(encoding="utf-8")
    logrotate = bundle.logrotate_config.read_text(encoding="utf-8")

    assert "ExecStart=python examples/demo_paper_loop.py" in systemd
    assert f"EnvironmentFile={env_file.resolve()}" in systemd
    assert "smc-ta-test.stdout.log" in systemd
    assert "ProgramArguments" in plist
    assert "demo_paper_loop.py" in plist
    assert "rotate 7" in logrotate
    assert bundle.readme.exists()


def test_configure_runtime_logging_writes_json_lines(tmp_path) -> None:
    logger = configure_runtime_logging(
        RuntimeLogConfig(
            log_dir=tmp_path,
            logger_name="smc_ta_test_runtime_logger",
            file_name="bot.log",
            max_bytes=10_000,
            backup_count=1,
            include_console=False,
            json_lines=True,
        )
    )

    logger.info("cycle_complete", extra={"symbol": "EURUSD", "action": "blocked"})
    for handler in logger.handlers:
        handler.flush()

    payload = json.loads((tmp_path / "bot.log").read_text(encoding="utf-8").splitlines()[-1])
    assert payload["message"] == "cycle_complete"
    assert payload["symbol"] == "EURUSD"
    assert payload["action"] == "blocked"
