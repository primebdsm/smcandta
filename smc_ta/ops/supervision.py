"""Process supervision artifact generation for bot deployments."""

from __future__ import annotations

import plistlib
import shlex
from dataclasses import dataclass
from pathlib import Path

from smc_ta.ops.logging import LogrotateConfig, render_logrotate_config


@dataclass(frozen=True)
class SupervisorConfig:
    """Settings used to render process supervisor files."""

    service_name: str = "smc-ta-bot"
    description: str = "SMC TA Forex bot"
    command: tuple[str, ...] = ("python", "examples/demo_paper_loop.py")
    working_directory: str | Path = "."
    env_file: str | Path | None = None
    log_dir: str | Path = "logs"
    user: str | None = None
    restart_seconds: int = 10
    start_limit_interval_seconds: int = 300
    start_limit_burst: int = 3
    timeout_stop_seconds: int = 30
    environment: dict[str, str] | None = None

    @property
    def stdout_log(self) -> Path:
        return Path(self.log_dir) / f"{self.service_name}.stdout.log"

    @property
    def stderr_log(self) -> Path:
        return Path(self.log_dir) / f"{self.service_name}.stderr.log"


@dataclass(frozen=True)
class SupervisorArtifactBundle:
    """Paths written by `write_supervisor_artifacts`."""

    output_dir: Path
    systemd_unit: Path
    launchd_plist: Path
    logrotate_config: Path
    readme: Path


def render_systemd_unit(config: SupervisorConfig) -> str:
    """Render a Linux systemd unit for the bot process."""

    cwd = _absolute_path(config.working_directory)
    env_file = _absolute_path(config.env_file) if config.env_file is not None else None
    stdout_log = _absolute_path(config.stdout_log)
    stderr_log = _absolute_path(config.stderr_log)
    lines = [
        "[Unit]",
        f"Description={config.description}",
        "Wants=network-online.target",
        "After=network-online.target",
        f"StartLimitIntervalSec={config.start_limit_interval_seconds}",
        f"StartLimitBurst={config.start_limit_burst}",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={cwd}",
        f"ExecStart={shlex.join(config.command)}",
        "Restart=always",
        f"RestartSec={config.restart_seconds}",
        f"TimeoutStopSec={config.timeout_stop_seconds}",
        "KillSignal=SIGINT",
        "Environment=PYTHONUNBUFFERED=1",
        f"StandardOutput=append:{stdout_log}",
        f"StandardError=append:{stderr_log}",
    ]
    if config.user:
        lines.append(f"User={config.user}")
    if env_file is not None:
        lines.append(f"EnvironmentFile={env_file}")
    for key, value in sorted((config.environment or {}).items()):
        lines.append(f"Environment={key}={shlex.quote(str(value))}")
    lines.extend(["", "[Install]", "WantedBy=multi-user.target", ""])
    return "\n".join(lines)


def render_launchd_plist(config: SupervisorConfig) -> str:
    """Render a macOS launchd plist for the bot process."""

    env = {"PYTHONUNBUFFERED": "1"}
    env.update(config.environment or {})
    payload = {
        "Label": config.service_name,
        "ProgramArguments": list(config.command),
        "WorkingDirectory": str(_absolute_path(config.working_directory)),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(_absolute_path(config.stdout_log)),
        "StandardErrorPath": str(_absolute_path(config.stderr_log)),
        "EnvironmentVariables": env,
    }
    return plistlib.dumps(payload, sort_keys=True).decode("utf-8")


def write_supervisor_artifacts(
    config: SupervisorConfig | None = None,
    output_dir: str | Path = "deployment/supervisor",
    *,
    logrotate: LogrotateConfig | None = None,
) -> SupervisorArtifactBundle:
    """Write systemd, launchd, logrotate, and operator README artifacts."""

    cfg = config or SupervisorConfig()
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    Path(cfg.log_dir).mkdir(parents=True, exist_ok=True)

    systemd_unit = root / f"{cfg.service_name}.service"
    launchd_plist = root / f"{cfg.service_name}.plist"
    logrotate_config = root / f"{cfg.service_name}.logrotate"
    readme = root / "README.md"

    systemd_unit.write_text(render_systemd_unit(cfg), encoding="utf-8")
    launchd_plist.write_text(render_launchd_plist(cfg), encoding="utf-8")
    logrotate_policy = logrotate or LogrotateConfig(name=cfg.service_name, log_glob=Path(cfg.log_dir) / "*.log")
    logrotate_config.write_text(render_logrotate_config(logrotate_policy), encoding="utf-8")
    readme.write_text(_render_supervisor_readme(cfg, systemd_unit, launchd_plist, logrotate_config), encoding="utf-8")

    return SupervisorArtifactBundle(
        output_dir=root,
        systemd_unit=systemd_unit,
        launchd_plist=launchd_plist,
        logrotate_config=logrotate_config,
        readme=readme,
    )


def _render_supervisor_readme(
    config: SupervisorConfig,
    systemd_unit: Path,
    launchd_plist: Path,
    logrotate_config: Path,
) -> str:
    return "\n".join(
        [
            f"# {config.service_name} Supervisor Artifacts",
            "",
            "Generated files:",
            "",
            f"- systemd unit: `{systemd_unit}`",
            f"- launchd plist: `{launchd_plist}`",
            f"- logrotate config: `{logrotate_config}`",
            "",
            "Review paths, credentials, and command arguments before installing these files.",
            "",
            "Linux systemd example:",
            "",
            "```bash",
            f"sudo cp {systemd_unit} /etc/systemd/system/{config.service_name}.service",
            "sudo systemctl daemon-reload",
            f"sudo systemctl enable --now {config.service_name}",
            f"sudo systemctl status {config.service_name}",
            "```",
            "",
            "macOS launchd example:",
            "",
            "```bash",
            f"cp {launchd_plist} ~/Library/LaunchAgents/{config.service_name}.plist",
            f"launchctl load ~/Library/LaunchAgents/{config.service_name}.plist",
            f"launchctl list | grep {config.service_name}",
            "```",
            "",
        ]
    )


def _absolute_path(value: str | Path | None) -> Path:
    if value is None:
        raise ValueError("path value cannot be None")
    return Path(value).expanduser().resolve()
