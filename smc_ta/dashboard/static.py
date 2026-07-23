"""Static HTML dashboard for local monitoring."""

from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd

from smc_ta.monitoring.metrics import performance_summary


def render_dashboard_html(
    *,
    symbol: str,
    signals: pd.DataFrame,
    features: pd.DataFrame,
    equity_curve: pd.DataFrame | None = None,
    trades: pd.DataFrame | None = None,
    blocked_events: pd.DataFrame | None = None,
) -> str:
    """Render a dependency-free local dashboard page."""

    latest_signal = signals.iloc[-1] if not signals.empty else pd.Series(dtype="object")
    latest_features = features.iloc[-1] if not features.empty else pd.Series(dtype="object")
    summary = performance_summary(equity_curve, trades) if equity_curve is not None and not equity_curve.empty else {}
    blocks = blocked_events.tail(20) if blocked_events is not None and not blocked_events.empty else pd.DataFrame()
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SMC TA Dashboard</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f7f7f4; color: #1d2528; }}
    header {{ padding: 20px 28px; background: #102a2d; color: white; }}
    main {{ padding: 24px; display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }}
    section {{ border: 1px solid #d8d8d0; border-radius: 6px; padding: 16px; background: white; }}
    h1, h2 {{ margin: 0 0 10px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    td, th {{ border-bottom: 1px solid #ecece7; padding: 7px; text-align: left; vertical-align: top; }}
    .side {{ font-size: 30px; font-weight: 700; }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(symbol.upper())} SMC TA Dashboard</h1>
  </header>
  <main>
    <section>
      <h2>Current Signal</h2>
      <div class="side">{escape(str(latest_signal.get("side", "flat")))}</div>
      {_dict_table({"confidence": latest_signal.get("confidence"), "long_score": latest_signal.get("long_score"), "short_score": latest_signal.get("short_score"), "reasons": latest_signal.get("reasons")})}
    </section>
    <section>
      <h2>SMC Context</h2>
      {_dict_table({"trend": latest_features.get("structure_trend"), "pd_zone": latest_features.get("pd_zone"), "sweep": latest_features.get("liquidity_sweep"), "bull_fvg_distance": latest_features.get("active_bull_fvg_distance"), "bear_fvg_distance": latest_features.get("active_bear_fvg_distance")})}
    </section>
    <section>
      <h2>Performance</h2>
      {_dict_table(summary)}
    </section>
    <section>
      <h2>Recent Trades</h2>
      {_frame_table(trades.tail(10) if trades is not None and not trades.empty else pd.DataFrame())}
    </section>
    <section>
      <h2>Blocked Events</h2>
      {_frame_table(blocks)}
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


def _dict_table(values: dict) -> str:
    rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(_format_value(value))}</td></tr>"
        for key, value in values.items()
    )
    return f"<table>{rows}</table>"


def _frame_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<p>No rows.</p>"
    safe = frame.copy()
    safe.columns = [escape(str(col)) for col in safe.columns]
    return safe.to_html(index=False, escape=True, border=0)


def _format_value(value) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.5f}"
    return str(value)

