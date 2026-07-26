"""Performance analytics dashboard for demo-forward evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Literal, Mapping

import pandas as pd

PerformanceSourceType = Literal["demo_forward_report", "demo_forward_schedule"]


@dataclass(frozen=True)
class PerformanceAnalyticsDashboardConfig:
    """Rendering settings for the performance analytics dashboard."""

    title: str = "SMC TA Performance Analytics"
    max_table_rows: int = 25
    refresh_seconds: int | None = None


@dataclass(frozen=True)
class PerformanceAnalyticsData:
    """Loaded demo-forward artifacts for dashboard rendering."""

    source_dir: Path
    source_type: PerformanceSourceType
    latest_run_dir: Path | None
    summary: Mapping[str, Any]
    scheduler_history: pd.DataFrame
    run_metrics: pd.DataFrame
    equity_curve: pd.DataFrame
    trades: pd.DataFrame
    fills: pd.DataFrame
    cycles: pd.DataFrame
    setup_report: pd.DataFrame
    session_report: pd.DataFrame
    daily_report: pd.DataFrame
    blocked_reasons: pd.DataFrame
    position_events: pd.DataFrame

    @property
    def status(self) -> str:
        if not self.scheduler_history.empty and str(self.scheduler_history.iloc[-1].get("status", "")) == "failed":
            return "failed"
        if self.summary and not _to_bool(self.summary.get("health_ok", True)):
            return "warning"
        if self.summary or not self.scheduler_history.empty:
            return "ok"
        return "warning"


def load_performance_analytics_data(source_dir: str | Path) -> PerformanceAnalyticsData:
    """Load a demo-forward report directory or scheduler output directory."""

    source = Path(source_dir)
    if not source.exists():
        raise FileNotFoundError(f"performance analytics source does not exist: {source}")

    history = _read_csv(source / "history.csv")
    if not history.empty:
        source_type: PerformanceSourceType = "demo_forward_schedule"
        latest_run_dir = _latest_report_dir(source, history)
        run_metrics = _load_run_metrics(source, history)
    else:
        source_type = "demo_forward_report"
        latest_run_dir = source
        run_metrics = pd.DataFrame()

    summary = _read_json(latest_run_dir / "summary.json") if latest_run_dir is not None else {}
    return PerformanceAnalyticsData(
        source_dir=source,
        source_type=source_type,
        latest_run_dir=latest_run_dir,
        summary=summary,
        scheduler_history=history,
        run_metrics=run_metrics,
        equity_curve=_read_csv(latest_run_dir / "equity_curve.csv") if latest_run_dir is not None else pd.DataFrame(),
        trades=_read_csv(latest_run_dir / "trades.csv") if latest_run_dir is not None else pd.DataFrame(),
        fills=_read_csv(latest_run_dir / "fills.csv") if latest_run_dir is not None else pd.DataFrame(),
        cycles=_read_csv(latest_run_dir / "cycles.csv") if latest_run_dir is not None else pd.DataFrame(),
        setup_report=_read_csv(latest_run_dir / "setup_report.csv") if latest_run_dir is not None else pd.DataFrame(),
        session_report=_read_csv(latest_run_dir / "session_report.csv") if latest_run_dir is not None else pd.DataFrame(),
        daily_report=_read_csv(latest_run_dir / "daily_report.csv") if latest_run_dir is not None else pd.DataFrame(),
        blocked_reasons=_read_csv(latest_run_dir / "blocked_reasons.csv") if latest_run_dir is not None else pd.DataFrame(),
        position_events=_read_csv(latest_run_dir / "position_events.csv") if latest_run_dir is not None else pd.DataFrame(),
    )


def render_performance_analytics_dashboard(
    data: PerformanceAnalyticsData,
    config: PerformanceAnalyticsDashboardConfig | None = None,
) -> str:
    """Render a dependency-free HTML performance analytics dashboard."""

    cfg = config or PerformanceAnalyticsDashboardConfig()
    summary = dict(data.summary)
    title = cfg.title
    status = data.status
    latest_dir = str(data.latest_run_dir) if data.latest_run_dir is not None else "not_available"
    kpis = {
        "Symbol": summary.get("symbol"),
        "Cycles": summary.get("cycles"),
        "Orders": summary.get("orders"),
        "Trades": summary.get("trades"),
        "Net PnL": summary.get("net_pnl"),
        "Return": summary.get("total_return_percent"),
        "Max Drawdown": summary.get("max_drawdown_percent"),
        "Win Rate": summary.get("win_rate_percent"),
        "Profit Factor": summary.get("profit_factor"),
        "Final Equity": summary.get("final_equity") or summary.get("end_equity"),
        "Blocked Cycles": summary.get("blocked_cycles"),
        "Position Events": summary.get("position_events"),
    }
    setup = _sort_numeric(data.setup_report, "net_pnl", ascending=False).head(cfg.max_table_rows)
    sessions = _sort_numeric(data.session_report, "net_pnl", ascending=False).head(cfg.max_table_rows)
    daily = _tail(data.daily_report, cfg.max_table_rows)
    blocks = _sort_numeric(data.blocked_reasons, "count", ascending=False).head(cfg.max_table_rows)
    trades = _sort_numeric(data.trades, "closed_at", ascending=False).head(cfg.max_table_rows)
    history = _tail(data.scheduler_history, cfg.max_table_rows)
    run_metrics = _tail(data.run_metrics, cfg.max_table_rows)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {_refresh_meta(cfg.refresh_seconds)}
  <title>{escape(title)}</title>
  <style>
    :root {{
      --ink: #172025;
      --muted: #5e6d75;
      --line: #d8dfdd;
      --surface: #ffffff;
      --band: #f2f4f3;
      --blue: #1f5f99;
      --teal: #0f766e;
      --amber: #a86112;
      --red: #b42318;
      --plum: #704264;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; color: var(--ink); background: var(--band); }}
    header {{ background: #25323a; color: white; padding: 18px 24px; }}
    header .top {{ display: flex; justify-content: space-between; align-items: center; gap: 16px; flex-wrap: wrap; }}
    h1 {{ margin: 0; font-size: 24px; line-height: 1.2; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; letter-spacing: 0; }}
    main {{ padding: 18px; display: grid; gap: 14px; grid-template-columns: repeat(12, minmax(0, 1fr)); }}
    section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px; min-width: 0; }}
    .span-12 {{ grid-column: span 12; }}
    .span-8 {{ grid-column: span 8; }}
    .span-6 {{ grid-column: span 6; }}
    .span-4 {{ grid-column: span 4; }}
    .status {{ display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 999px; font-weight: 700; font-size: 13px; background: white; color: var(--ink); }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; background: var(--muted); display: inline-block; }}
    .status.ok .dot {{ background: var(--teal); }}
    .status.warning .dot {{ background: var(--amber); }}
    .status.failed .dot {{ background: var(--red); }}
    .meta {{ margin-top: 8px; color: #dbe5e7; display: flex; gap: 12px; flex-wrap: wrap; font-size: 13px; }}
    .metrics {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(135px, 1fr)); gap: 10px; }}
    .metric {{ min-height: 64px; background: #fbfcfc; border-left: 4px solid var(--teal); padding: 8px 10px; }}
    .metric:nth-child(3n) {{ border-left-color: var(--blue); }}
    .metric:nth-child(4n) {{ border-left-color: var(--plum); }}
    .metric span {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    .metric strong {{ display: block; margin-top: 5px; font-size: 18px; overflow-wrap: anywhere; }}
    .chart svg {{ width: 100%; height: 190px; display: block; }}
    .small-chart svg {{ height: 150px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #edf1ef; padding: 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ color: #33454d; font-weight: 700; background: #f7f9f8; }}
    .table-wrap {{ overflow-x: auto; }}
    .empty {{ color: var(--muted); margin: 8px 0 0; font-size: 13px; }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; padding: 12px; }}
      .span-12, .span-8, .span-6, .span-4 {{ grid-column: span 1; }}
      header {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="top">
      <h1>{escape(title)}</h1>
      {_status_badge(status)}
    </div>
    <div class="meta">
      <span>source: {escape(data.source_type)}</span>
      <span>directory: {escape(str(data.source_dir))}</span>
      <span>latest run: {escape(latest_dir)}</span>
      <span>period: {escape(_period(summary))}</span>
    </div>
  </header>
  <main>
    <section class="span-12">
      <div class="metrics">{''.join(_metric(label, value, suffix=_suffix(label)) for label, value in kpis.items())}</div>
    </section>
    <section class="span-8 chart">
      <h2>Equity Curve</h2>
      {_line_svg(data.equity_curve, "equity", stroke="var(--blue)", label="equity")}
    </section>
    <section class="span-4 chart small-chart">
      <h2>Drawdown</h2>
      {_drawdown_svg(data.equity_curve)}
    </section>
    <section class="span-6 chart small-chart">
      <h2>Setup Net PnL</h2>
      {_bar_svg(setup, "setup_name", "net_pnl", positive="var(--teal)", negative="var(--red)")}
    </section>
    <section class="span-6 chart small-chart">
      <h2>Run Final Equity</h2>
      {_line_svg(data.run_metrics, "final_equity", stroke="var(--plum)", label="final equity")}
    </section>
    <section class="span-6">
      <h2>Setup Performance</h2>
      {_frame_table(setup)}
    </section>
    <section class="span-6">
      <h2>Session Performance</h2>
      {_frame_table(sessions)}
    </section>
    <section class="span-6">
      <h2>Daily Performance</h2>
      {_frame_table(daily)}
    </section>
    <section class="span-6">
      <h2>Blocked Reasons</h2>
      {_frame_table(blocks)}
    </section>
    <section class="span-6">
      <h2>Recent Trades</h2>
      {_frame_table(trades)}
    </section>
    <section class="span-6">
      <h2>Scheduler History</h2>
      {_frame_table(history)}
    </section>
    <section class="span-12">
      <h2>Run Metrics</h2>
      {_frame_table(run_metrics)}
    </section>
  </main>
</body>
</html>"""


def write_performance_analytics_dashboard(
    source_dir: str | Path,
    output_path: str | Path,
    *,
    config: PerformanceAnalyticsDashboardConfig | None = None,
) -> Path:
    """Load demo-forward artifacts and write the performance analytics dashboard."""

    data = load_performance_analytics_data(source_dir)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_performance_analytics_dashboard(data, config=config), encoding="utf-8")
    return output


def _latest_report_dir(source: Path, history: pd.DataFrame) -> Path | None:
    if history.empty:
        return None
    if "status" in history.columns:
        history = history.loc[history["status"].astype(str).isin(("ok", "warning"))]
    if history.empty:
        return None
    row = history.iloc[-1]
    for column in ("output_dir", "summary_json"):
        value = row.get(column)
        if value is None or pd.isna(value) or str(value).strip() == "":
            continue
        path = _resolve_recorded_path(source, str(value))
        return path.parent if column == "summary_json" else path
    return None


def _load_run_metrics(source: Path, history: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for _, row in history.iterrows():
        record = row.to_dict()
        summary_path = row.get("summary_json")
        summary = _read_json(_resolve_recorded_path(source, str(summary_path))) if _has_text(summary_path) else {}
        rows.append(
            {
                "run_id": record.get("run_id"),
                "status": record.get("status"),
                "started_at": record.get("started_at"),
                "last_candle_time": record.get("last_candle_time"),
                "cycles": _first(summary.get("cycles"), record.get("cycles")),
                "orders": _first(summary.get("orders"), record.get("orders")),
                "trades": _first(summary.get("trades"), record.get("trades")),
                "blocked_cycles": _first(summary.get("blocked_cycles"), record.get("blocked_cycles")),
                "net_pnl": summary.get("net_pnl"),
                "total_return_percent": summary.get("total_return_percent"),
                "max_drawdown_percent": summary.get("max_drawdown_percent"),
                "win_rate_percent": summary.get("win_rate_percent"),
                "profit_factor": summary.get("profit_factor"),
                "final_equity": _first(summary.get("final_equity"), record.get("final_equity")),
                "message": record.get("message"),
            }
        )
    return pd.DataFrame(rows)


def _resolve_recorded_path(source: Path, value: str) -> Path:
    path = Path(value)
    if path.exists() or path.is_absolute():
        return path
    source_candidate = source / path
    if source_candidate.exists():
        return source_candidate
    return path


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def _sort_numeric(frame: pd.DataFrame, column: str, *, ascending: bool) -> pd.DataFrame:
    if frame.empty or column not in frame.columns:
        return frame
    copy = frame.copy()
    values = pd.to_numeric(copy[column], errors="coerce")
    return copy.assign(_sort_value=values).sort_values("_sort_value", ascending=ascending).drop(columns=["_sort_value"])


def _tail(frame: pd.DataFrame, rows: int) -> pd.DataFrame:
    return frame.tail(rows) if frame is not None and not frame.empty else pd.DataFrame()


def _frame_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return '<p class="empty">No rows.</p>'
    safe = frame.copy().reset_index(drop=True)
    for column in safe.columns:
        safe[column] = safe[column].map(_format_value)
    return f'<div class="table-wrap">{safe.to_html(index=False, escape=True, border=0)}</div>'


def _metric(label: str, value: object, *, suffix: str = "") -> str:
    return f'<div class="metric"><span>{escape(label)}</span><strong>{escape(_format_value(value, suffix=suffix))}</strong></div>'


def _status_badge(status: str) -> str:
    return f'<span class="status {escape(status)}"><span class="dot"></span>{escape(status.upper())}</span>'


def _refresh_meta(refresh_seconds: int | None) -> str:
    if refresh_seconds is None or refresh_seconds <= 0:
        return ""
    return f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'


def _line_svg(frame: pd.DataFrame, column: str, *, stroke: str, label: str) -> str:
    if frame.empty or column not in frame.columns:
        return '<p class="empty">No rows.</p>'
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return '<p class="empty">No rows.</p>'
    width = 720
    height = 190
    pad = 18
    points = _line_points(values, width=width, height=height, pad=pad)
    start = _format_value(values.iloc[0])
    end = _format_value(values.iloc[-1])
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfcfc"></rect>
  <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#d8dfdd"></line>
  <polyline fill="none" stroke="{escape(stroke)}" stroke-width="3" points="{points}"></polyline>
  <text x="{pad}" y="18" fill="#5e6d75" font-size="12">start {escape(start)}</text>
  <text x="{width - pad}" y="18" text-anchor="end" fill="#5e6d75" font-size="12">end {escape(end)}</text>
</svg>"""


def _drawdown_svg(equity_curve: pd.DataFrame) -> str:
    if equity_curve.empty or "equity" not in equity_curve.columns:
        return '<p class="empty">No rows.</p>'
    equity = pd.to_numeric(equity_curve["equity"], errors="coerce").dropna()
    if equity.empty:
        return '<p class="empty">No rows.</p>'
    drawdown = (equity / equity.cummax() - 1.0) * 100.0
    return _line_svg(pd.DataFrame({"drawdown": drawdown}), "drawdown", stroke="var(--red)", label="drawdown")


def _bar_svg(frame: pd.DataFrame, label_col: str, value_col: str, *, positive: str, negative: str) -> str:
    if frame.empty or label_col not in frame.columns or value_col not in frame.columns:
        return '<p class="empty">No rows.</p>'
    data = frame[[label_col, value_col]].copy().head(8)
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce").fillna(0.0)
    if data.empty:
        return '<p class="empty">No rows.</p>'
    width = 720
    row_h = 24
    height = max(70, 28 + len(data) * row_h)
    label_w = 165
    max_abs = max(float(data[value_col].abs().max()), 1e-9)
    zero_x = label_w + 250
    scale = (width - zero_x - 24) / max_abs
    rows = []
    for _, row in data.iterrows():
        value = float(row[value_col])
        y = 24 + len(rows) * row_h
        bar_w = abs(value) * scale
        x = zero_x if value >= 0 else zero_x - bar_w
        color = positive if value >= 0 else negative
        text_anchor = "start" if value >= 0 else "end"
        value_x = zero_x + 6 if value >= 0 else x - 6
        label = escape(str(row[label_col]))[:24]
        formatted_value = escape(_format_value(value))
        rows.append(
            f'<text x="10" y="{y + 12}" fill="#33454d" font-size="12">{label}</text>'
            f'<rect x="{x:.2f}" y="{y}" width="{bar_w:.2f}" height="14" fill="{escape(color)}"></rect>'
            f'<text x="{value_x:.2f}" y="{y + 12}" text-anchor="{text_anchor}" '
            f'fill="#5e6d75" font-size="12">{formatted_value}</text>'
        )
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(value_col)} by {escape(label_col)}">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfcfc"></rect>
  <line x1="{zero_x}" y1="16" x2="{zero_x}" y2="{height - 8}" stroke="#d8dfdd"></line>
  {''.join(rows)}
</svg>"""


def _line_points(values: pd.Series, *, width: int, height: int, pad: int) -> str:
    if len(values) == 1:
        y = height / 2
        return f"{pad},{y:.2f} {width - pad},{y:.2f}"
    min_v = float(values.min())
    max_v = float(values.max())
    span = max(max_v - min_v, 1e-9)
    coords = []
    for idx, value in enumerate(values):
        x = pad + idx * ((width - 2 * pad) / (len(values) - 1))
        y = height - pad - ((float(value) - min_v) / span) * (height - 2 * pad)
        coords.append(f"{x:.2f},{y:.2f}")
    return " ".join(coords)


def _period(summary: Mapping[str, Any]) -> str:
    start = summary.get("start_time")
    end = summary.get("end_time")
    if start and end:
        return f"{start} to {end}"
    return "not_available"


def _suffix(label: str) -> str:
    return "%" if label in {"Return", "Max Drawdown", "Win Rate"} else ""


def _format_value(value: object, *, suffix: str = "") -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        return "inf" if value == float("inf") else f"{value:.4f}{suffix}"
    if isinstance(value, int):
        return f"{value}{suffix}"
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_format_value(item) for item in value)
    return f"{value}{suffix}"


def _to_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _has_text(value: object) -> bool:
    return value is not None and not pd.isna(value) and str(value).strip() != ""


def _first(primary: object, fallback: object) -> object:
    return primary if primary is not None else fallback
