"""Runtime logging helpers for supervised bot processes."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Literal

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogrotateFrequency = Literal["daily", "weekly", "monthly"]


@dataclass(frozen=True)
class RuntimeLogConfig:
    """File and console logging settings for demo/live bot processes."""

    log_dir: str | Path = "logs"
    logger_name: str = "smc_ta"
    file_name: str = "bot.log"
    level: LogLevel = "INFO"
    max_bytes: int = 10_000_000
    backup_count: int = 10
    include_console: bool = True
    json_lines: bool = False

    @property
    def log_path(self) -> Path:
        return Path(self.log_dir) / self.file_name


@dataclass(frozen=True)
class LogrotateConfig:
    """External logrotate policy for supervised deployment logs."""

    name: str = "smc-ta"
    log_glob: str | Path = "logs/*.log"
    frequency: LogrotateFrequency = "daily"
    rotate_count: int = 14
    compress: bool = True
    missing_ok: bool = True
    copytruncate: bool = True
    notifempty: bool = True
    create_mode: str | None = None
    max_size: str | None = None


def configure_runtime_logging(config: RuntimeLogConfig | None = None) -> logging.Logger:
    """Configure a logger with rotating file output and optional console output."""

    cfg = config or RuntimeLogConfig()
    log_path = cfg.log_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(cfg.logger_name)
    logger.setLevel(getattr(logging, cfg.level.upper()))
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if getattr(handler, "_smc_ta_runtime_handler", False):
            logger.removeHandler(handler)
            handler.close()

    formatter: logging.Formatter
    if cfg.json_lines:
        formatter = _JsonLineFormatter()
        formatter.converter = time_gmt
    else:
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%SZ",
        )
        formatter.converter = time_gmt

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=cfg.max_bytes,
        backupCount=cfg.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(getattr(logging, cfg.level.upper()))
    file_handler._smc_ta_runtime_handler = True  # type: ignore[attr-defined]
    logger.addHandler(file_handler)

    if cfg.include_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(getattr(logging, cfg.level.upper()))
        console_handler._smc_ta_runtime_handler = True  # type: ignore[attr-defined]
        logger.addHandler(console_handler)

    logger.debug("runtime_logging_configured", extra={"runtime_log_config": asdict(cfg)})
    return logger


def render_logrotate_config(config: LogrotateConfig | None = None) -> str:
    """Render a logrotate config block."""

    cfg = config or LogrotateConfig()
    lines = [f"{cfg.log_glob} {{", f"    {cfg.frequency}", f"    rotate {cfg.rotate_count}"]
    if cfg.max_size:
        lines.append(f"    size {cfg.max_size}")
    if cfg.missing_ok:
        lines.append("    missingok")
    if cfg.notifempty:
        lines.append("    notifempty")
    if cfg.compress:
        lines.append("    compress")
    if cfg.copytruncate:
        lines.append("    copytruncate")
    if cfg.create_mode:
        lines.append(f"    create {cfg.create_mode}")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def write_logrotate_config(config: LogrotateConfig | None, path: str | Path) -> Path:
    """Write a logrotate config file."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_logrotate_config(config), encoding="utf-8")
    return output


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _LOG_RECORD_KEYS:
                continue
            payload[key] = _jsonable(value)
        return json.dumps(payload, sort_keys=True)


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def time_gmt(*args):  # noqa: ANN002, ANN003
    """Return GMT time tuple for logging formatters."""

    import time

    return time.gmtime(*args)


_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}
