"""Static HTML dashboards for local/live monitoring."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Iterable

import pandas as pd

from smc_ta.broker.models import AccountState, Position
from smc_ta.lifecycle import TradeLifecycleRecord
from smc_ta.monitoring.live import LiveMonitoringSnapshot, build_live_monitoring_snapshot
from smc_ta.preflight import PreflightReport
from smc_ta.safety import EmergencyStopResult


def render_dashboard_html(
    *,
    symbol: str,
    signals: pd.DataFrame,
    features: pd.DataFrame,
    equity_curve: pd.DataFrame | None = None,
    trades: pd.DataFrame | None = None,
    blocked_events: pd.DataFrame | None = None,
    account: AccountState | None = None,
    open_positions: Iterable[Position] | None = None,
    preflight: PreflightReport | None = None,
    emergency_stop: EmergencyStopResult | None = None,
    lifecycle_records: Iterable[TradeLifecycleRecord] | None = None,
    journal_events: pd.DataFrame | None = None,
    execution_samples: pd.DataFrame | None = None,
    mode: str = "paper",
    broker_name: str = "paper",
    refresh_seconds: int | None = None,
) -> str:
    """Render a dependency-free local dashboard page."""

    snapshot = build_live_monitoring_snapshot(
        symbol=symbol,
        signals=signals,
        features=features,
        account=account,
        open_positions=open_positions,
        equity_curve=equity_curve,
        trades=trades,
        blocked_events=blocked_events,
        preflight=preflight,
        emergency_stop=emergency_stop,
        lifecycle_records=lifecycle_records,
        journal_events=journal_events,
        execution_samples=execution_samples,
        mode=mode,
        broker_name=broker_name,
    )
    return render_live_dashboard_html(snapshot, refresh_seconds=refresh_seconds)


def render_live_dashboard_html(
    snapshot: LiveMonitoringSnapshot,
    *,
    refresh_seconds: int | None = None,
    title: str | None = None,
) -> str:
    """Render a full live/demo monitoring dashboard from one snapshot."""

    status = snapshot.status
    title_text = title or f"SMC TA Dashboard - {snapshot.symbol} Live Monitor"
    signal = snapshot.latest_signal
    features = snapshot.latest_features
    account = snapshot.account_dict()
    positions = snapshot.positions_frame()
    lifecycle = snapshot.lifecycle_frame(tail=20)
    journal = snapshot.journal_events.tail(20) if not snapshot.journal_events.empty else pd.DataFrame()
    blocks = snapshot.blocked_events.tail(20) if not snapshot.blocked_events.empty else pd.DataFrame()
    executions = snapshot.execution_samples.tail(20) if not snapshot.execution_samples.empty else pd.DataFrame()
    preflight = snapshot.preflight_frame()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {_refresh_meta(refresh_seconds)}
  <title>{escape(title_text)}</title>
  <style>
    :root {{
      --ink: #1d2528;
      --muted: #627177;
      --line: #d9dfdc;
      --surface: #ffffff;
      --band: #f6f7f4;
      --ok: #13795b;
      --warn: #a76705;
      --block: #b42318;
      --blue: #1b5f9e;
      --violet: #6f4aa5;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; color: var(--ink); background: var(--band); }}
    header {{ padding: 18px 24px; background: #263238; color: white; }}
    header .top {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
    h1 {{ margin: 0; font-size: 24px; line-height: 1.2; letter-spacing: 0; }}
    h2 {{ margin: 0 0 10px; font-size: 16px; letter-spacing: 0; }}
    main {{ padding: 20px; display: grid; gap: 16px; grid-template-columns: repeat(12, minmax(0, 1fr)); }}
    section {{ background: var(--surface); border: 1px solid var(--line); border-radius: 6px; padding: 14px; min-width: 0; }}
    .span-12 {{ grid-column: span 12; }}
    .span-8 {{ grid-column: span 8; }}
    .span-6 {{ grid-column: span 6; }}
    .span-4 {{ grid-column: span 4; }}
    .span-3 {{ grid-column: span 3; }}
    .status {{ display: inline-flex; align-items: center; gap: 8px; padding: 6px 10px; border-radius: 999px; font-weight: 700; font-size: 13px; background: white; color: var(--ink); }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; background: var(--muted); }}
    .status.ok .dot {{ background: var(--ok); }}
    .status.warning .dot {{ background: var(--warn); }}
    .status.blocking .dot {{ background: var(--block); }}
    .meta {{ margin-top: 8px; color: #d9e3e4; font-size: 13px; display: flex; gap: 12px; flex-wrap: wrap; }}
    .metrics {{ display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }}
    .metric {{ border-left: 4px solid var(--blue); padding: 8px 10px; background: #f9fbfb; min-height: 62px; }}
    .metric strong {{ display: block; font-size: 18px; margin-top: 4px; overflow-wrap: anywhere; }}
    .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; }}
    .side {{ font-size: 30px; font-weight: 700; overflow-wrap: anywhere; }}
    .side.long, .side.buy {{ color: var(--ok); }}
    .side.short, .side.sell {{ color: var(--block); }}
    .side.flat {{ color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td, th {{ border-bottom: 1px solid #edf0ee; padding: 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }}
    th {{ color: #37474f; font-weight: 700; }}
    .table-wrap {{ overflow-x: auto; }}
    .empty {{ color: var(--muted); margin: 6px 0 0; font-size: 13px; }}
    .reason-list {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
    .reason {{ border: 1px solid var(--line); border-radius: 999px; padding: 4px 8px; font-size: 12px; background: #fbfbfa; }}
    .chart svg {{ width: 100%; height: 160px; display: block; }}
    @media (max-width: 900px) {{
      main {{ grid-template-columns: 1fr; padding: 12px; }}
      .span-12, .span-8, .span-6, .span-4, .span-3 {{ grid-column: span 1; }}
      header {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="top">
      <h1>{escape(title_text)}</h1>
      {_status_badge(status)}
    </div>
    <div class="meta">
      <span>{escape(snapshot.timestamp.isoformat())}</span>
      <span>mode: {escape(snapshot.mode)}</span>
      <span>broker: {escape(snapshot.broker_name)}</span>
      <span>positions: {snapshot.open_position_count}</span>
      <span>active lifecycle: {snapshot.active_lifecycle_count}</span>
    </div>
  </header>
  <main>
    <section class="span-12">
      <div class="metrics">
        {_metric("Equity", account.get("equity"))}
        {_metric("Balance", account.get("balance"))}
        {_metric("Free Margin", account.get("free_margin"))}
        {_metric("Drawdown", snapshot.performance.get("max_drawdown_percent"), suffix="%")}
        {_metric("Win Rate", snapshot.performance.get("win_rate_percent"), suffix="%")}
        {_metric("Profit Factor", snapshot.performance.get("profit_factor"))}
      </div>
      {_reason_list(snapshot.blocking_reasons, snapshot.warning_reasons)}
    </section>
    <section class="span-4">
      <h2>Current Signal</h2>
      <div class="side {escape(str(signal.get("side", "flat")).lower())}">{escape(str(signal.get("side", "flat")))}</div>
      {_dict_table({"confidence": signal.get("confidence"), "long_score": signal.get("long_score"), "short_score": signal.get("short_score"), "entry": signal.get("entry_reference"), "stop": signal.get("stop_reference"), "target": signal.get("target_reference"), "reasons": signal.get("reasons")})}
    </section>
    <section class="span-4">
      <h2>SMC / TA Context</h2>
      {_dict_table({"trend": features.get("structure_trend"), "pd_zone": features.get("pd_zone"), "sweep": features.get("liquidity_sweep"), "bull_fvg_distance": features.get("active_bull_fvg_distance"), "bear_fvg_distance": features.get("active_bear_fvg_distance"), "atr": features.get("atr"), "rsi": features.get("rsi")})}
    </section>
    <section class="span-4">
      <h2>Safety State</h2>
      {_dict_table({"preflight": snapshot.preflight.summary() if snapshot.preflight else None, "emergency_stop": snapshot.emergency_stop.summary() if snapshot.emergency_stop else "not_provided", "health": ";".join(snapshot.health_messages), "blocked_events": len(blocks), "journal_events": len(journal)})}
    </section>
    <section class="span-8 chart">
      <h2>Equity Curve</h2>
      {_equity_svg(snapshot.equity_curve)}
    </section>
    <section class="span-4">
      <h2>Performance</h2>
      {_dict_table(snapshot.performance)}
    </section>
    <section class="span-6">
      <h2>Open Positions</h2>
      {_frame_table(positions)}
    </section>
    <section class="span-6">
      <h2>Preflight Checks</h2>
      {_frame_table(_tail(preflight, 16))}
    </section>
    <section class="span-6">
      <h2>Lifecycle</h2>
      {_frame_table(lifecycle)}
    </section>
    <section class="span-6">
      <h2>Journal Events</h2>
      {_frame_table(journal)}
    </section>
    <section class="span-6">
      <h2>Blocked Events</h2>
      {_frame_table(blocks)}
    </section>
    <section class="span-6">
      <h2>Execution Samples</h2>
      {_frame_table(executions)}
    </section>
  </main>
</body>
</html>"""


def write_dashboard(path: str | Path, **kwargs) -> Path:
    """Write the dashboard HTML to disk."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard_html(**kwargs), encoding="utf-8")
    return output


def write_live_dashboard(
    path: str | Path,
    snapshot: LiveMonitoringSnapshot,
    *,
    refresh_seconds: int | None = None,
    title: str | None = None,
) -> Path:
    """Write a live monitoring dashboard HTML file."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_live_dashboard_html(snapshot, refresh_seconds=refresh_seconds, title=title),
        encoding="utf-8",
    )
    return output


def _refresh_meta(refresh_seconds: int | None) -> str:
    if refresh_seconds is None or refresh_seconds <= 0:
        return ""
    return f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'


def _status_badge(status: str) -> str:
    return f'<span class="status {escape(status)}"><span class="dot"></span>{escape(status.upper())}</span>'


def _metric(label: str, value: object, *, suffix: str = "") -> str:
    return f'<div class="metric"><span class="label">{escape(label)}</span><strong>{escape(_format_value(value, suffix=suffix))}</strong></div>'


def _reason_list(blocking: tuple[str, ...], warnings: tuple[str, ...]) -> str:
    reasons = [("blocking", reason) for reason in blocking] + [("warning", reason) for reason in warnings]
    if not reasons:
        return '<div class="reason-list"><span class="reason">ok</span></div>'
    chips = "".join(f'<span class="reason">{escape(level)}: {escape(reason)}</span>' for level, reason in reasons)
    return f'<div class="reason-list">{chips}</div>'


def _dict_table(values: dict) -> str:
    clean = {key: value for key, value in values.items() if value is not None}
    if not clean:
        return '<p class="empty">No rows.</p>'
    rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(_format_value(value))}</td></tr>"
        for key, value in clean.items()
    )
    return f'<div class="table-wrap"><table>{rows}</table></div>'


def _frame_table(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty:
        return '<p class="empty">No rows.</p>'
    safe = frame.copy()
    safe = safe.reset_index(drop=True)
    safe.columns = [escape(str(col)) for col in safe.columns]
    for column in safe.columns:
        safe[column] = safe[column].map(_format_value)
    return f'<div class="table-wrap">{safe.to_html(index=False, escape=True, border=0)}</div>'


def _tail(frame: pd.DataFrame, rows: int) -> pd.DataFrame:
    return frame.tail(rows) if frame is not None and not frame.empty else pd.DataFrame()


def _equity_svg(equity_curve: pd.DataFrame) -> str:
    if equity_curve is None or equity_curve.empty or "equity" not in equity_curve.columns:
        return '<p class="empty">No rows.</p>'
    values = pd.to_numeric(equity_curve["equity"], errors="coerce").dropna()
    if values.empty:
        return '<p class="empty">No rows.</p>'
    width = 720
    height = 160
    pad = 14
    if len(values) == 1:
        y = height / 2
        points = f"{pad},{y:.2f} {width - pad},{y:.2f}"
    else:
        min_v = float(values.min())
        max_v = float(values.max())
        span = max(max_v - min_v, 1e-9)
        coords = []
        for idx, value in enumerate(values):
            x = pad + idx * ((width - 2 * pad) / (len(values) - 1))
            y = height - pad - ((float(value) - min_v) / span) * (height - 2 * pad)
            coords.append(f"{x:.2f},{y:.2f}")
        points = " ".join(coords)
    start = _format_value(values.iloc[0])
    end = _format_value(values.iloc[-1])
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="equity curve">
  <rect x="0" y="0" width="{width}" height="{height}" fill="#fbfcfb"></rect>
  <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#d9dfdc"></line>
  <polyline fill="none" stroke="#1b5f9e" stroke-width="3" points="{points}"></polyline>
  <text x="{pad}" y="18" fill="#627177" font-size="12">start {escape(start)}</text>
  <text x="{width - pad}" y="18" text-anchor="end" fill="#627177" font-size="12">end {escape(end)}</text>
</svg>"""


def _format_value(value: object, *, suffix: str = "") -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return ", ".join(_format_value(item) for item in value)
    if isinstance(value, dict):
        return "; ".join(f"{key}={_format_value(val)}" for key, val in value.items())
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        if value == float("inf"):
            return "inf"
        return f"{value:.5f}{suffix}"
    if isinstance(value, int):
        return f"{value}{suffix}"
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return f"{value}{suffix}"
