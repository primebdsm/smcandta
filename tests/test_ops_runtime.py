from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from smc_ta import (
    CommandSecretSource,
    EnvSecretSource,
    JsonSecretSource,
    LogrotateConfig,
    OandaCredentialOnboardingConfig,
    RuntimeLogConfig,
    SecretResolutionConfig,
    SupervisorConfig,
    check_oanda_credential_onboarding,
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


def test_oanda_credential_onboarding_accepts_prefixed_env_and_redacts_report(tmp_path) -> None:
    result = check_oanda_credential_onboarding(
        OandaCredentialOnboardingConfig(output_report=tmp_path / "oanda_credentials.json"),
        env={
            "SMC_TA_OANDA_ACCOUNT_ID": "practice-account-id",
            "SMC_TA_OANDA_TOKEN": "practice-token",
        },
    )
    report_text = result.output_report.read_text(encoding="utf-8")

    assert result.ok
    assert result.summary() == "oanda_credentials_ok"
    assert result.accepted_keys == ("SMC_TA_OANDA_ACCOUNT_ID", "SMC_TA_OANDA_TOKEN")
    assert result.secret_report.used_sources["SMC_TA_OANDA_TOKEN"] == "env_smc_ta"
    assert "practice-token" not in report_text
    assert "practice-account-id" not in report_text
    assert "--broker oanda" in result.startup_command()


def test_oanda_credential_onboarding_reports_missing_keys_and_export_templates(tmp_path) -> None:
    env_file = tmp_path / ".env.demo"
    env_file.write_text("", encoding="utf-8")

    result = check_oanda_credential_onboarding(
        OandaCredentialOnboardingConfig(
            env_file=env_file,
            startup_output_dir=tmp_path / "startup",
        ),
        env={},
    )

    assert not result.ok
    assert result.summary() == "oanda_credentials_blocked:missing_required_secret"
    assert result.missing_keys == ("OANDA_ACCOUNT_ID", "OANDA_TOKEN")
    assert "export SMC_TA_OANDA_ACCOUNT_ID=..." in result.export_templates()
    assert f"--env-file {env_file}" in result.startup_command()


def test_oanda_credential_onboarding_blocks_example_placeholders() -> None:
    result = check_oanda_credential_onboarding(
        OandaCredentialOnboardingConfig(env_file=".env.demo.example"),
        env={},
    )

    assert not result.ok
    assert result.summary() == "oanda_credentials_blocked:placeholder_secret_value"
    assert set(result.accepted_keys) == {"SMC_TA_OANDA_ACCOUNT_ID", "SMC_TA_OANDA_TOKEN"}
    assert {
        issue.key
        for issue in result.secret_report.blocking_issues
        if issue.code == "placeholder_secret_value"
    } == {"SMC_TA_OANDA_ACCOUNT_ID", "SMC_TA_OANDA_TOKEN"}


def test_oanda_credential_onboarding_cli_blocks_without_printing_secrets(tmp_path) -> None:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OANDA_ACCOUNT_ID", "OANDA_TOKEN", "SMC_TA_OANDA_ACCOUNT_ID", "SMC_TA_OANDA_TOKEN"}
    }
    completed = subprocess.run(
        [
            sys.executable,
            "examples/onboard_oanda_credentials.py",
            "--output",
            str(tmp_path / "credentials.json"),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "oanda_credentials_blocked:missing_required_secret" in completed.stdout
    assert "missing_keys=OANDA_ACCOUNT_ID,OANDA_TOKEN" in completed.stdout
    assert "export SMC_TA_OANDA_TOKEN=..." in completed.stdout
    assert "OANDA_TOKEN=" not in completed.stdout.replace("export SMC_TA_OANDA_TOKEN=...", "")


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
