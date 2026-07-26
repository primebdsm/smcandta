"""Risk stress testing for demo-forward Forex evidence."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field, replace
from html import escape
from pathlib import Path
from typing import Any, Literal, Mapping

import pandas as pd

from smc_ta.forwardtest import DemoForwardConfig, DemoForwardResult, run_demo_forward_test, write_demo_forward_report_bundle
from smc_ta.forex.pairs import infer_pip_size
from smc_ta.validation import normalize_ohlcv

RiskStressStatus = Literal["ok", "warning", "failed"]


@dataclass(frozen=True)
class RiskStressScenario:
    """One execution or market-condition stress scenario."""

    name: str
    spread_multiplier: float = 1.0
    additional_spread_pips: float = 0.0
    slippage_multiplier: float = 1.0
    additional_slippage_pips: float = 0.0
    commission_multiplier: float = 1.0
    additional_commission_per_order: float = 0.0
    risk_percent_multiplier: float = 1.0
    max_units_multiplier: float = 1.0
    range_multiplier: float = 1.0


@dataclass(frozen=True)
class RiskStressConfig:
    """Settings for a risk stress test run."""

    demo_forward: DemoForwardConfig = field(default_factory=DemoForwardConfig)
    scenarios: tuple[RiskStressScenario, ...] = field(default_factory=lambda: DEFAULT_RISK_STRESS_SCENARIOS)
    max_allowed_drawdown_percent: float | None = 15.0
    min_final_equity: float | None = None
    min_net_pnl: float | None = None
    max_return_degradation_percent: float | None = None
    continue_on_failure: bool = True


@dataclass(frozen=True)
class RiskStressScenarioResult:
    """Result for one stress scenario."""

    scenario: RiskStressScenario
    status: RiskStressStatus
    message: str
    result: DemoForwardResult | None = None
    summary: Mapping[str, Any] = field(default_factory=dict)
    net_pnl_delta: float | None = None
    final_equity_delta: float | None = None
    return_delta: float | None = None
    drawdown_delta: float | None = None
    output_dir: Path | None = None
    summary_json: Path | None = None
    html_report: Path | None = None
    exception_type: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def warning(self) -> bool:
        return self.status == "warning"

    @property
    def failed(self) -> bool:
        return self.status == "failed"

    def to_dict(self) -> dict[str, Any]:
        summary = dict(self.summary)
        return {
            "scenario": self.scenario.name,
            "status": self.status,
            "message": self.message,
            "spread_multiplier": self.scenario.spread_multiplier,
            "additional_spread_pips": self.scenario.additional_spread_pips,
            "slippage_multiplier": self.scenario.slippage_multiplier,
            "additional_slippage_pips": self.scenario.additional_slippage_pips,
            "commission_multiplier": self.scenario.commission_multiplier,
            "additional_commission_per_order": self.scenario.additional_commission_per_order,
            "risk_percent_multiplier": self.scenario.risk_percent_multiplier,
            "max_units_multiplier": self.scenario.max_units_multiplier,
            "range_multiplier": self.scenario.range_multiplier,
            "cycles": summary.get("cycles"),
            "orders": summary.get("orders"),
            "trades": summary.get("trades"),
            "blocked_cycles": summary.get("blocked_cycles"),
            "net_pnl": summary.get("net_pnl"),
            "final_equity": summary.get("final_equity") or summary.get("end_equity"),
            "total_return_percent": summary.get("total_return_percent"),
            "max_drawdown_percent": summary.get("max_drawdown_percent"),
            "win_rate_percent": summary.get("win_rate_percent"),
            "profit_factor": summary.get("profit_factor"),
            "net_pnl_delta": self.net_pnl_delta,
            "final_equity_delta": self.final_equity_delta,
            "return_delta": self.return_delta,
            "drawdown_delta": self.drawdown_delta,
            "output_dir": str(self.output_dir) if self.output_dir is not None else None,
            "summary_json": str(self.summary_json) if self.summary_json is not None else None,
            "html_report": str(self.html_report) if self.html_report is not None else None,
            "exception_type": self.exception_type,
        }


@dataclass(frozen=True)
class RiskStressReportBundle:
    """Paths written by `write_risk_stress_report_bundle`."""

    output_dir: Path
    summary_json: Path
    scenarios_csv: Path
    html_report: Path


@dataclass(frozen=True)
class RiskStressResult:
    """Complete risk stress test result."""

    config: RiskStressConfig
    scenarios: tuple[RiskStressScenarioResult, ...]
    artifacts: RiskStressReportBundle | None = None

    @property
    def ok(self) -> bool:
        return bool(self.scenarios) and not any(item.failed or item.warning for item in self.scenarios)

    def summary(self) -> str:
        if not self.scenarios:
            return "risk_stress_no_scenarios"
        failed = sum(1 for item in self.scenarios if item.failed)
        warnings = sum(1 for item in self.scenarios if item.warning)
        ok = sum(1 for item in self.scenarios if item.ok)
        if failed:
            return f"risk_stress_failed:failed={failed};warning={warnings};ok={ok}"
        if warnings:
            return f"risk_stress_warning:warning={warnings};ok={ok}"
        return f"risk_stress_ok:ok={ok}"

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame([item.to_dict() for item in self.scenarios])

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "summary": self.summary(),
            "config": _config_dict(self.config),
            "scenarios": [item.to_dict() for item in self.scenarios],
            "artifacts": _bundle_dict(self.artifacts),
        }

    def with_artifacts(self, artifacts: RiskStressReportBundle) -> "RiskStressResult":
        return replace(self, artifacts=artifacts)

    def with_scenarios(self, scenarios: tuple[RiskStressScenarioResult, ...]) -> "RiskStressResult":
        return replace(self, scenarios=scenarios)


DEFAULT_RISK_STRESS_SCENARIOS: tuple[RiskStressScenario, ...] = (
    RiskStressScenario("baseline"),
    RiskStressScenario("wide_spread", spread_multiplier=2.0, additional_spread_pips=0.5),
    RiskStressScenario("high_slippage", slippage_multiplier=4.0, additional_slippage_pips=0.3),
    RiskStressScenario(
        "costly_execution",
        spread_multiplier=1.5,
        additional_spread_pips=0.5,
        slippage_multiplier=2.0,
        additional_slippage_pips=0.2,
        additional_commission_per_order=2.0,
    ),
    RiskStressScenario("volatility_spike", spread_multiplier=1.5, range_multiplier=1.75),
    RiskStressScenario("half_risk", risk_percent_multiplier=0.5, max_units_multiplier=0.5),
)


def run_risk_stress_test(
    candles: pd.DataFrame,
    *,
    config: RiskStressConfig | None = None,
) -> RiskStressResult:
    """Run demo-forward stress scenarios and compare against baseline."""

    cfg = config or RiskStressConfig()
    _validate_config(cfg)
    baseline_summary: Mapping[str, Any] | None = None
    scenario_results: list[RiskStressScenarioResult] = []
    for scenario in cfg.scenarios:
        try:
            scenario_result = _run_scenario(candles, cfg, scenario, baseline_summary)
        except Exception as exc:
            scenario_result = RiskStressScenarioResult(
                scenario=scenario,
                status="failed",
                message=f"risk_stress_scenario_failed:{type(exc).__name__}:{exc}",
                exception_type=type(exc).__name__,
            )
        if baseline_summary is None and scenario_result.result is not None:
            baseline_summary = scenario_result.summary
            scenario_result = _with_deltas_and_status(scenario_result, cfg, baseline_summary)
        scenario_results.append(scenario_result)
        if scenario_result.failed and not cfg.continue_on_failure:
            break
    return RiskStressResult(config=cfg, scenarios=tuple(scenario_results))


def write_risk_stress_report_bundle(result: RiskStressResult, output_dir: str | Path) -> RiskStressResult:
    """Write risk stress JSON, CSV, HTML, and per-scenario demo-forward reports."""

    root = Path(output_dir)
    scenarios_root = root / "scenarios"
    root.mkdir(parents=True, exist_ok=True)
    scenarios_root.mkdir(parents=True, exist_ok=True)
    updated: list[RiskStressScenarioResult] = []
    for item in result.scenarios:
        if item.result is None:
            updated.append(item)
            continue
        scenario_dir = scenarios_root / _safe_name(item.scenario.name)
        saved = write_demo_forward_report_bundle(item.result, scenario_dir)
        artifacts = saved.artifacts
        updated.append(
            replace(
                item,
                result=saved,
                output_dir=artifacts.output_dir if artifacts is not None else scenario_dir,
                summary_json=artifacts.summary_json if artifacts is not None else None,
                html_report=artifacts.html_report if artifacts is not None else None,
            )
        )
    result = result.with_scenarios(tuple(updated))
    summary_json = root / "summary.json"
    scenarios_csv = root / "scenarios.csv"
    html_report = root / "stress_report.html"
    artifacts = RiskStressReportBundle(
        output_dir=root,
        summary_json=summary_json,
        scenarios_csv=scenarios_csv,
        html_report=html_report,
    )
    result = result.with_artifacts(artifacts)
    scenarios_csv.write_text(result.to_frame().to_csv(index=False), encoding="utf-8")
    summary_json.write_text(json.dumps(_jsonable(result.to_safe_dict()), indent=2, sort_keys=True), encoding="utf-8")
    html_report.write_text(render_risk_stress_report_html(result), encoding="utf-8")
    return result


def render_risk_stress_report_html(result: RiskStressResult) -> str:
    """Render a standalone HTML stress report."""

    frame = result.to_frame()
    title = "SMC TA Risk Stress Test"
    status = "OK" if result.ok else "CHECK"
    worst = _worst_case(frame)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      --ink: #172025;
      --muted: #5e6d75;
      --line: #d8dfdd;
      --surface: #ffffff;
      --band: #f3f5f4;
      --blue: #1f5f99;
      --teal: #0f766e;
      --amber: #a86112;
      --red: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; color: var(--ink); background: var(--band); }}
    header {{ background: #263238; color: white; padding: 18px 24px; }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; letter-spacing: 0; }}
    main {{ display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 14px; padding: 18px; }}
    section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px; min-width: 0; }}
    .span-12 {{ grid-column: span 12; }}
    .span-6 {{ grid-column: span 6; }}
    .meta {{ margin-top: 8px; color: #dce6e8; display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(145px, 1fr)); gap: 10px; }}
    .metric {{ min-height: 64px; background: #fbfcfc; border-left: 4px solid var(--blue); padding: 8px 10px; }}
    .metric:nth-child(2n) {{ border-left-color: var(--teal); }}
    .metric span {{ color: var(--muted); display: block; font-size: 12px; text-transform: uppercase; }}
    .metric strong {{ display: block; margin-top: 5px; font-size: 18px; overflow-wrap: anywhere; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #edf1ef; padding: 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ background: #f7f9f8; color: #33454d; }}
    .table-wrap {{ overflow-x: auto; }}
    .chart svg {{ width: 100%; height: 190px; display: block; }}
    .ok {{ color: var(--teal); }}
    .warning {{ color: var(--amber); }}
    .failed {{ color: var(--red); }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; padding: 12px; }}
      .span-12, .span-6 {{ grid-column: span 1; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>{title} <span class="{escape(status.lower())}">{escape(status)}</span></h1>
    <div class="meta">
      <span>{escape(result.summary())}</span>
      <span>scenarios: {len(result.scenarios)}</span>
      <span>max drawdown gate: {escape(_format_value(result.config.max_allowed_drawdown_percent, suffix="%"))}</span>
    </div>
  </header>
  <main>
    <section class="span-12">
      <div class="metrics">
        {_metric("Worst Scenario", worst.get("scenario"))}
        {_metric("Worst Net PnL", worst.get("net_pnl"))}
        {_metric("Worst Drawdown", worst.get("max_drawdown_percent"), suffix="%")}
        {_metric("Worst Return Delta", worst.get("return_delta"), suffix="%")}
        {_metric("Warnings", sum(1 for item in result.scenarios if item.warning))}
        {_metric("Failures", sum(1 for item in result.scenarios if item.failed))}
      </div>
    </section>
    <section class="span-6 chart">
      <h2>Net PnL By Scenario</h2>
      {_bar_svg(frame, "scenario", "net_pnl")}
    </section>
    <section class="span-6 chart">
      <h2>Return Delta By Scenario</h2>
      {_bar_svg(frame, "scenario", "return_delta")}
    </section>
    <section class="span-12">
      <h2>Scenario Summary</h2>
      {_frame_table(frame)}
    </section>
  </main>
</body>
</html>"""


def _run_scenario(
    candles: pd.DataFrame,
    cfg: RiskStressConfig,
    scenario: RiskStressScenario,
    baseline_summary: Mapping[str, Any] | None,
) -> RiskStressScenarioResult:
    stressed_candles = _apply_scenario_to_candles(candles, cfg.demo_forward, scenario)
    stressed_config = _apply_scenario_to_config(cfg.demo_forward, scenario)
    forward_result = run_demo_forward_test(stressed_candles, config=stressed_config)
    scenario_result = RiskStressScenarioResult(
        scenario=scenario,
        status="ok",
        message="risk_stress_scenario_ok",
        result=forward_result,
        summary=forward_result.summary,
    )
    if baseline_summary is None:
        baseline_summary = forward_result.summary
    return _with_deltas_and_status(scenario_result, cfg, baseline_summary)


def _with_deltas_and_status(
    result: RiskStressScenarioResult,
    cfg: RiskStressConfig,
    baseline_summary: Mapping[str, Any],
) -> RiskStressScenarioResult:
    summary = dict(result.summary)
    net_pnl_delta = _optional_float(summary.get("net_pnl")) - _optional_float(baseline_summary.get("net_pnl"))
    final_equity_delta = _final_equity(summary) - _final_equity(baseline_summary)
    return_delta = _optional_float(summary.get("total_return_percent")) - _optional_float(
        baseline_summary.get("total_return_percent")
    )
    drawdown_delta = _optional_float(summary.get("max_drawdown_percent")) - _optional_float(
        baseline_summary.get("max_drawdown_percent")
    )
    warnings = _scenario_warnings(summary, cfg, return_delta)
    if result.result is not None and not result.result.ok:
        warnings.extend(str(message) for message in summary.get("health_messages", ()))
    status: RiskStressStatus = "warning" if warnings else "ok"
    return replace(
        result,
        status=status,
        message=";".join(warnings) if warnings else "risk_stress_scenario_ok",
        net_pnl_delta=net_pnl_delta,
        final_equity_delta=final_equity_delta,
        return_delta=return_delta,
        drawdown_delta=drawdown_delta,
    )


def _apply_scenario_to_config(cfg: DemoForwardConfig, scenario: RiskStressScenario) -> DemoForwardConfig:
    risk = cfg.risk
    max_units = risk.max_units
    if max_units is not None:
        max_units = max_units * scenario.max_units_multiplier
    stressed_risk = replace(
        risk,
        risk_percent_per_trade=risk.risk_percent_per_trade * scenario.risk_percent_multiplier,
        max_units=max_units,
    )
    return replace(
        cfg,
        default_spread_pips=cfg.default_spread_pips * scenario.spread_multiplier + scenario.additional_spread_pips,
        slippage_pips=cfg.slippage_pips * scenario.slippage_multiplier + scenario.additional_slippage_pips,
        commission_per_order=(
            cfg.commission_per_order * scenario.commission_multiplier + scenario.additional_commission_per_order
        ),
        risk=stressed_risk,
    )


def _apply_scenario_to_candles(
    candles: pd.DataFrame,
    cfg: DemoForwardConfig,
    scenario: RiskStressScenario,
) -> pd.DataFrame:
    data = normalize_ohlcv(candles).copy()
    pip_size = infer_pip_size(cfg.symbol)
    base_spread = data["spread"] if "spread" in data.columns else cfg.default_spread_pips * pip_size
    data["spread"] = base_spread * scenario.spread_multiplier + scenario.additional_spread_pips * pip_size
    if scenario.range_multiplier != 1.0:
        high = data[["high", "open", "close"]].max(axis=1)
        low = data[["low", "open", "close"]].min(axis=1)
        center = (high + low) / 2.0
        half = (high - low) * scenario.range_multiplier / 2.0
        data["high"] = pd.concat([data["open"], data["close"], center + half], axis=1).max(axis=1)
        data["low"] = pd.concat([data["open"], data["close"], center - half], axis=1).min(axis=1)
    return data


def _scenario_warnings(summary: Mapping[str, Any], cfg: RiskStressConfig, return_delta: float) -> list[str]:
    warnings: list[str] = []
    drawdown = abs(_optional_float(summary.get("max_drawdown_percent")))
    if cfg.max_allowed_drawdown_percent is not None and drawdown > cfg.max_allowed_drawdown_percent:
        warnings.append("max_drawdown_stress_limit_exceeded")
    final_equity = _final_equity(summary)
    if cfg.min_final_equity is not None and final_equity < cfg.min_final_equity:
        warnings.append("final_equity_below_stress_minimum")
    net_pnl = _optional_float(summary.get("net_pnl"))
    if cfg.min_net_pnl is not None and net_pnl < cfg.min_net_pnl:
        warnings.append("net_pnl_below_stress_minimum")
    if cfg.max_return_degradation_percent is not None and return_delta < -abs(cfg.max_return_degradation_percent):
        warnings.append("return_degradation_limit_exceeded")
    return warnings


def _validate_config(cfg: RiskStressConfig) -> None:
    if not cfg.scenarios:
        raise ValueError("at least one risk stress scenario is required")
    names = [scenario.name for scenario in cfg.scenarios]
    if len(names) != len(set(names)):
        raise ValueError("risk stress scenario names must be unique")
    for scenario in cfg.scenarios:
        if not scenario.name.strip():
            raise ValueError("risk stress scenario name must not be empty")
        for field_name, value in asdict(scenario).items():
            if field_name == "name":
                continue
            if float(value) < 0:
                raise ValueError(f"{scenario.name}.{field_name} must be >= 0")


def _worst_case(frame: pd.DataFrame) -> dict[str, Any]:
    if frame.empty:
        return {}
    if "net_pnl" in frame.columns:
        values = pd.to_numeric(frame["net_pnl"], errors="coerce")
        if values.notna().any():
            return frame.loc[values.idxmin()].to_dict()
    return frame.iloc[0].to_dict()


def _bar_svg(frame: pd.DataFrame, label_col: str, value_col: str) -> str:
    if frame.empty or label_col not in frame.columns or value_col not in frame.columns:
        return '<p>No rows.</p>'
    data = frame[[label_col, value_col]].copy().head(8)
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce").fillna(0.0)
    width = 720
    row_h = 24
    height = max(70, 28 + len(data) * row_h)
    zero_x = 360
    max_abs = max(float(data[value_col].abs().max()), 1e-9)
    scale = (width - zero_x - 24) / max_abs
    rows = []
    for _, row in data.iterrows():
        value = float(row[value_col])
        y = 24 + len(rows) * row_h
        bar_w = abs(value) * scale
        x = zero_x if value >= 0 else zero_x - bar_w
        color = "var(--teal)" if value >= 0 else "var(--red)"
        value_x = zero_x + 6 if value >= 0 else x - 6
        anchor = "start" if value >= 0 else "end"
        rows.append(
            f'<text x="10" y="{y + 12}" fill="#33454d" font-size="12">{escape(str(row[label_col]))[:28]}</text>'
            f'<rect x="{x:.2f}" y="{y}" width="{bar_w:.2f}" height="14" fill="{color}"></rect>'
            f'<text x="{value_x:.2f}" y="{y + 12}" text-anchor="{anchor}" fill="#5e6d75" font-size="12">'
            f'{escape(_format_value(value))}</text>'
        )
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(value_col)} by {escape(label_col)}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfcfc"></rect>
  <line x1="{zero_x}" y1="16" x2="{zero_x}" y2="{height - 8}" stroke="#d8dfdd"></line>
  {''.join(rows)}
</svg>"""


def _frame_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return "<p>No rows.</p>"
    safe = frame.copy().reset_index(drop=True)
    for column in safe.columns:
        safe[column] = safe[column].map(_format_value)
    return f'<div class="table-wrap">{safe.to_html(index=False, escape=True, border=0)}</div>'


def _metric(label: str, value: object, *, suffix: str = "") -> str:
    return f'<div class="metric"><span>{escape(label)}</span><strong>{escape(_format_value(value, suffix=suffix))}</strong></div>'


def _config_dict(cfg: RiskStressConfig) -> dict[str, Any]:
    record = asdict(cfg)
    return _jsonable(record)


def _bundle_dict(bundle: RiskStressReportBundle | None) -> dict[str, str] | None:
    if bundle is None:
        return None
    return {key: str(value) for key, value in asdict(bundle).items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        if math.isnan(value):
            return None
        return value
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value.strip().lower())


def _final_equity(summary: Mapping[str, Any]) -> float:
    return _optional_float(summary.get("final_equity") or summary.get("end_equity"))


def _optional_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str) and value.strip().lower() in {"inf", "+inf"}:
        return float("inf")
    if isinstance(value, str) and value.strip().lower() == "-inf":
        return float("-inf")
    try:
        if pd.isna(value):
            return 0.0
    except (TypeError, ValueError):
        pass
    return float(value)


def _format_value(value: object, *, suffix: str = "") -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return f"{value:.4f}{suffix}"
    if isinstance(value, int):
        return f"{value}{suffix}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_format_value(item) for item in value)
    return f"{value}{suffix}"
