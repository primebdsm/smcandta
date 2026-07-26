"""Demo-forward report scheduler."""

from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd

from smc_ta.data import load_csv_candles
from smc_ta.forwardtest.runner import (
    DemoForwardConfig,
    DemoForwardResult,
    run_demo_forward_test,
    write_demo_forward_report_bundle,
)
from smc_ta.validation import normalize_ohlcv

DemoForwardScheduleStatus = Literal["ok", "warning", "failed", "skipped"]
CandlesLoader = Callable[[], pd.DataFrame]
SleepFn = Callable[[float], None]
NowFn = Callable[[], object]


@dataclass(frozen=True)
class DemoForwardScheduleConfig:
    """Settings for repeated demo-forward report generation."""

    output_dir: str | Path = "reports/demo_forward_scheduler"
    csv_path: str | Path | None = None
    interval_seconds: float = 900.0
    max_runs: int | None = 1
    skip_when_no_new_candle: bool = True
    stop_on_failure: bool = False
    run_label_prefix: str = "demo_forward"
    demo_forward: DemoForwardConfig = field(default_factory=DemoForwardConfig)


@dataclass(frozen=True)
class DemoForwardScheduledRun:
    """One scheduled demo-forward attempt."""

    run_id: str
    status: DemoForwardScheduleStatus
    started_at: pd.Timestamp
    finished_at: pd.Timestamp
    last_candle_time: pd.Timestamp | None = None
    output_dir: Path | None = None
    summary_json: Path | None = None
    html_report: Path | None = None
    cycles: int = 0
    orders: int = 0
    trades: int = 0
    blocked_cycles: int = 0
    final_equity: float | None = None
    message: str = ""
    exception_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": _utc_timestamp(self.started_at).isoformat(),
            "finished_at": _utc_timestamp(self.finished_at).isoformat(),
            "last_candle_time": _timestamp_or_none(self.last_candle_time),
            "output_dir": str(self.output_dir) if self.output_dir is not None else None,
            "summary_json": str(self.summary_json) if self.summary_json is not None else None,
            "html_report": str(self.html_report) if self.html_report is not None else None,
            "cycles": self.cycles,
            "orders": self.orders,
            "trades": self.trades,
            "blocked_cycles": self.blocked_cycles,
            "final_equity": self.final_equity,
            "message": self.message,
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True)
class DemoForwardScheduleResult:
    """Result of a bounded demo-forward scheduler run."""

    config: DemoForwardScheduleConfig
    runs: tuple[DemoForwardScheduledRun, ...]
    output_dir: Path
    history_csv: Path
    summary_json: Path

    @property
    def ok(self) -> bool:
        return bool(self.runs) and not any(run.failed or run.warning for run in self.runs)

    def summary(self) -> str:
        if not self.runs:
            return "demo_forward_schedule_no_runs"
        failed = sum(1 for run in self.runs if run.failed)
        warnings = sum(1 for run in self.runs if run.warning)
        skipped = sum(1 for run in self.runs if run.skipped)
        ok = sum(1 for run in self.runs if run.ok)
        if failed:
            return f"demo_forward_schedule_failed:failed={failed};ok={ok};warning={warnings};skipped={skipped}"
        if warnings:
            return f"demo_forward_schedule_warning:warning={warnings};ok={ok};skipped={skipped}"
        if skipped and not ok:
            return f"demo_forward_schedule_skipped:skipped={skipped}"
        return f"demo_forward_schedule_ok:ok={ok};skipped={skipped}"

    def history_frame(self) -> pd.DataFrame:
        return pd.DataFrame([run.to_dict() for run in self.runs])

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "output_dir": str(self.output_dir),
            "history_csv": str(self.history_csv),
            "summary_json": str(self.summary_json),
            "config": _config_dict(self.config),
            "runs": [run.to_dict() for run in self.runs],
        }


def run_demo_forward_schedule(
    config: DemoForwardScheduleConfig | None = None,
    *,
    candles_loader: CandlesLoader | None = None,
    sleep_fn: SleepFn = time.sleep,
    now_fn: NowFn | None = None,
) -> DemoForwardScheduleResult:
    """Run a bounded or continuous demo-forward report schedule."""

    cfg = config or DemoForwardScheduleConfig()
    _validate_config(cfg)
    root = Path(cfg.output_dir)
    runs_dir = root / "runs"
    root.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)
    history_csv = root / "history.csv"
    summary_json = root / "schedule_summary.json"
    clock = now_fn or (lambda: pd.Timestamp.now(tz="UTC"))

    runs: list[DemoForwardScheduledRun] = []
    last_completed_candle = _latest_completed_candle(history_csv) if cfg.skip_when_no_new_candle else None
    run_index = 0
    while cfg.max_runs is None or run_index < cfg.max_runs:
        run_index += 1
        run = _run_once(
            cfg,
            candles_loader=candles_loader,
            runs_dir=runs_dir,
            run_index=run_index,
            last_completed_candle=last_completed_candle,
            now_fn=clock,
        )
        runs.append(run)
        if run.status in {"ok", "warning"} and run.last_candle_time is not None:
            last_completed_candle = run.last_candle_time
        result = DemoForwardScheduleResult(
            config=cfg,
            runs=tuple(runs),
            output_dir=root,
            history_csv=history_csv,
            summary_json=summary_json,
        )
        write_demo_forward_schedule_artifacts(result)
        if run.failed and cfg.stop_on_failure:
            break
        if cfg.max_runs is not None and run_index >= cfg.max_runs:
            break
        sleep_fn(cfg.interval_seconds)

    return DemoForwardScheduleResult(
        config=cfg,
        runs=tuple(runs),
        output_dir=root,
        history_csv=history_csv,
        summary_json=summary_json,
    )


def write_demo_forward_schedule_artifacts(result: DemoForwardScheduleResult) -> DemoForwardScheduleResult:
    """Write scheduler summary and flat run history."""

    result.output_dir.mkdir(parents=True, exist_ok=True)
    _combined_history(result.history_csv, result.history_frame()).to_csv(result.history_csv, index=False)
    result.summary_json.write_text(
        json.dumps(result.to_safe_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return result


def _run_once(
    cfg: DemoForwardScheduleConfig,
    *,
    candles_loader: CandlesLoader | None,
    runs_dir: Path,
    run_index: int,
    last_completed_candle: pd.Timestamp | None,
    now_fn: NowFn,
) -> DemoForwardScheduledRun:
    started_at = _utc_timestamp(now_fn())
    run_id = _run_id(cfg.run_label_prefix, run_index, started_at)
    try:
        candles = _load_candles(cfg, candles_loader)
        last_candle_time = _last_candle_time(candles)
        if (
            cfg.skip_when_no_new_candle
            and last_completed_candle is not None
            and last_candle_time == last_completed_candle
        ):
            return DemoForwardScheduledRun(
                run_id=run_id,
                status="skipped",
                started_at=started_at,
                finished_at=_utc_timestamp(now_fn()),
                last_candle_time=last_candle_time,
                message="no_new_candle",
            )
        result = run_demo_forward_test(candles, config=cfg.demo_forward)
        output_dir = runs_dir / run_id
        saved = write_demo_forward_report_bundle(result, output_dir)
        return _scheduled_run_from_result(
            run_id,
            saved,
            started_at=started_at,
            finished_at=_utc_timestamp(now_fn()),
            last_candle_time=last_candle_time,
        )
    except Exception as exc:
        return DemoForwardScheduledRun(
            run_id=run_id,
            status="failed",
            started_at=started_at,
            finished_at=_utc_timestamp(now_fn()),
            message=f"demo_forward_schedule_failed:{type(exc).__name__}:{exc}",
            exception_type=type(exc).__name__,
        )


def _scheduled_run_from_result(
    run_id: str,
    result: DemoForwardResult,
    *,
    started_at: pd.Timestamp,
    finished_at: pd.Timestamp,
    last_candle_time: pd.Timestamp,
) -> DemoForwardScheduledRun:
    summary = result.summary
    status: DemoForwardScheduleStatus = "ok" if result.ok else "warning"
    message = (
        "demo_forward_report_ok"
        if result.ok
        else ";".join(str(item) for item in summary.get("health_messages", ()))
    )
    artifacts = result.artifacts
    return DemoForwardScheduledRun(
        run_id=run_id,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        last_candle_time=last_candle_time,
        output_dir=artifacts.output_dir if artifacts is not None else None,
        summary_json=artifacts.summary_json if artifacts is not None else None,
        html_report=artifacts.html_report if artifacts is not None else None,
        cycles=int(summary.get("cycles", 0) or 0),
        orders=int(summary.get("orders", 0) or 0),
        trades=int(summary.get("trades", 0) or 0),
        blocked_cycles=int(summary.get("blocked_cycles", 0) or 0),
        final_equity=_optional_float(summary.get("final_equity")),
        message=message or "demo_forward_report_warning",
    )


def _load_candles(cfg: DemoForwardScheduleConfig, candles_loader: CandlesLoader | None) -> pd.DataFrame:
    if candles_loader is not None:
        return candles_loader()
    if cfg.csv_path is not None:
        return load_csv_candles(cfg.csv_path)
    return _sample_candles()


def _last_candle_time(candles: pd.DataFrame) -> pd.Timestamp:
    data = normalize_ohlcv(candles)
    if data.empty:
        raise ValueError("no candles available for demo-forward schedule")
    return _utc_timestamp(data.index[-1])


def _sample_candles(rows: int = 220) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    sequence = [float(i) for i in range(rows)]
    close = pd.Series(
        [1.1000 + math.sin(value / 5.0) * 0.001 + value * 0.00002 for value in sequence],
        index=index,
    )
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": [100 + (int(value) % 25) for value in sequence],
            "spread": 0.00012,
        },
        index=index,
    )


def _validate_config(cfg: DemoForwardScheduleConfig) -> None:
    if cfg.interval_seconds < 0:
        raise ValueError("interval_seconds must be >= 0")
    if cfg.max_runs is not None and cfg.max_runs <= 0:
        raise ValueError("max_runs must be positive or None")
    if not str(cfg.run_label_prefix).strip():
        raise ValueError("run_label_prefix must not be empty")


def _latest_completed_candle(history_csv: Path) -> pd.Timestamp | None:
    if not history_csv.exists():
        return None
    try:
        history = pd.read_csv(history_csv)
    except Exception:
        return None
    if history.empty or "status" not in history.columns or "last_candle_time" not in history.columns:
        return None
    eligible = history.loc[
        history["status"].isin(("ok", "warning"))
        & history["last_candle_time"].notna()
        & (history["last_candle_time"] != "")
    ]
    if eligible.empty:
        return None
    return _utc_timestamp(eligible.iloc[-1]["last_candle_time"])


def _combined_history(history_csv: Path, current: pd.DataFrame) -> pd.DataFrame:
    if not history_csv.exists():
        return current
    try:
        existing = pd.read_csv(history_csv)
    except Exception:
        return current
    if existing.empty or "run_id" not in existing.columns or "run_id" not in current.columns:
        return current
    new_rows = current.loc[~current["run_id"].isin(set(existing["run_id"].astype(str)))]
    return pd.concat([existing, new_rows], ignore_index=True)


def _config_dict(cfg: DemoForwardScheduleConfig) -> dict[str, Any]:
    record = asdict(cfg)
    record["output_dir"] = str(cfg.output_dir)
    record["csv_path"] = str(cfg.csv_path) if cfg.csv_path is not None else None
    return record


def _run_id(prefix: str, run_index: int, started_at: pd.Timestamp) -> str:
    safe_prefix = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in prefix.strip())
    stamp = _utc_timestamp(started_at).strftime("%Y%m%dT%H%M%SZ")
    return f"{safe_prefix}_{run_index:04d}_{stamp}"


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return float(value)


def _timestamp_or_none(value: pd.Timestamp | None) -> str | None:
    return _utc_timestamp(value).isoformat() if value is not None else None


def _utc_timestamp(value: object | None = None) -> pd.Timestamp:
    ts = pd.Timestamp.now(tz="UTC") if value is None else pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
