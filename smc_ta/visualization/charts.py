"""Dependency-free SMC/TA chart rendering."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

import numpy as np
import pandas as pd

from smc_ta.engine.confluence import AnalysisResult
from smc_ta.validation import normalize_ohlcv


@dataclass(frozen=True)
class ChartConfig:
    """Rendering settings for static SMC/TA charts."""

    width: int = 1280
    height: int = 760
    visible_bars: int = 160
    padding_left: int = 76
    padding_right: int = 98
    padding_top: int = 34
    padding_bottom: int = 76
    volume_height: int = 110
    show_volume: bool = True
    show_ema: bool = True
    show_vwap: bool = True
    show_smc_zones: bool = True
    show_liquidity: bool = True
    show_structure: bool = True
    show_signals: bool = True
    max_zones: int = 36
    max_markers: int = 80
    include_summary: bool = True


def render_analysis_chart_html(
    result: AnalysisResult,
    *,
    symbol: str | None = None,
    title: str | None = None,
    config: ChartConfig | None = None,
) -> str:
    """Render a complete standalone HTML chart from an analysis result."""

    cfg = config or ChartConfig()
    resolved_symbol = symbol or _infer_symbol(result.features, "FOREX")
    resolved_title = title or f"{resolved_symbol} SMC TA Chart"
    svg = render_analysis_chart_svg(
        result.candles,
        features=result.features,
        signals=result.signals,
        market_structure=result.market_structure,
        fair_value_gaps=result.fair_value_gaps,
        order_blocks=result.order_blocks,
        liquidity_pools=result.liquidity_pools,
        symbol=resolved_symbol,
        config=cfg,
    )
    latest_signal = result.signals.iloc[-1] if not result.signals.empty else pd.Series(dtype="object")
    latest_features = result.features.iloc[-1] if not result.features.empty else pd.Series(dtype="object")
    summary = _summary_table(
        {
            "side": latest_signal.get("side", "flat"),
            "confidence": latest_signal.get("confidence"),
            "long_score": latest_signal.get("long_score"),
            "short_score": latest_signal.get("short_score"),
            "structure": latest_features.get("structure_trend"),
            "pd_zone": latest_features.get("pd_zone"),
            "spread_pips": latest_features.get("spread_pips"),
            "reasons": latest_signal.get("reasons"),
        }
    )
    summary_block = f"<section class=\"summary\"><h2>Current Signal</h2>{summary}</section>" if cfg.include_summary else ""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(resolved_title)}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f5f5f1; color: #172326; }}
    header {{ padding: 18px 28px 12px; background: #163033; color: #ffffff; }}
    h1 {{ margin: 0; font-size: 24px; letter-spacing: 0; }}
    main {{ padding: 22px; display: grid; gap: 16px; }}
    .chart-frame {{ overflow-x: auto; border: 1px solid #d8d6cb; border-radius: 6px; background: #ffffff; }}
    .summary {{ border: 1px solid #d8d6cb; border-radius: 6px; padding: 14px 16px; background: #ffffff; }}
    .summary h2 {{ margin: 0 0 8px; font-size: 18px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #ece9dc; padding: 7px 8px; text-align: left; vertical-align: top; }}
    th {{ width: 150px; color: #455154; }}
  </style>
</head>
<body>
  <header>
    <h1>{escape(resolved_title)}</h1>
  </header>
  <main>
    <div class="chart-frame">{svg}</div>
    {summary_block}
  </main>
</body>
</html>"""


def render_analysis_chart_svg(
    candles: pd.DataFrame,
    *,
    features: pd.DataFrame | None = None,
    signals: pd.DataFrame | None = None,
    market_structure: pd.DataFrame | None = None,
    fair_value_gaps: pd.DataFrame | None = None,
    order_blocks: pd.DataFrame | None = None,
    liquidity_pools: pd.DataFrame | None = None,
    symbol: str = "FOREX",
    config: ChartConfig | None = None,
) -> str:
    """Render candles, TA overlays, SMC zones, liquidity, and signals as SVG."""

    cfg = config or ChartConfig()
    data = normalize_ohlcv(candles)
    if data.empty:
        return _empty_svg(cfg, "No candles")

    visible = data.tail(max(1, cfg.visible_bars))
    visible_features = _tail_reindex(features, visible.index)
    visible_signals = _tail_reindex(signals, visible.index)
    visible_structure = _tail_reindex(market_structure, visible.index)

    dims = _chart_dimensions(cfg)
    price_min, price_max = _price_domain(
        visible,
        visible_features,
        visible_signals,
        fair_value_gaps,
        order_blocks,
        visible.index,
    )
    y_price = _price_mapper(price_min, price_max, dims["price_top"], dims["price_bottom"])
    x_at = _position_mapper(len(visible), dims["plot_left"], dims["plot_right"])

    layers: list[str] = [
        _svg_defs(),
        _render_background(cfg, dims, price_min, price_max, visible.index),
    ]

    if cfg.show_smc_zones:
        layers.append(_render_zone_table(fair_value_gaps, visible.index, x_at, y_price, "fvg", cfg.max_zones))
        layers.append(_render_zone_table(order_blocks, visible.index, x_at, y_price, "ob", cfg.max_zones))

    if cfg.show_liquidity:
        layers.append(_render_liquidity_pools(liquidity_pools, visible.index, x_at, y_price, dims, cfg.max_zones))

    layers.append(_render_candles(visible, x_at, y_price, dims))

    if cfg.show_volume:
        layers.append(_render_volume(visible, x_at, dims))

    if cfg.show_ema:
        layers.append(_render_indicator_line(visible_features, "ema_20", visible.index, x_at, y_price, "ema20"))
        layers.append(_render_indicator_line(visible_features, "ema_50", visible.index, x_at, y_price, "ema50"))
    if cfg.show_vwap:
        layers.append(_render_indicator_line(visible_features, "vwap", visible.index, x_at, y_price, "vwap"))

    if cfg.show_structure:
        layers.append(_render_structure_markers(visible, visible_structure, visible.index, x_at, y_price, cfg.max_markers))

    if cfg.show_liquidity:
        layers.append(_render_sweep_markers(visible, visible_features, visible.index, x_at, y_price, cfg.max_markers))

    if cfg.show_signals:
        layers.append(_render_signal_markers(visible, visible_signals, visible.index, x_at, y_price, dims, cfg.max_markers))

    layers.append(_render_legend(dims, symbol))
    return f"""<svg class="smc-ta-chart candlestick-chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cfg.width} {cfg.height}" role="img" aria-label="{escape(symbol)} SMC TA chart">
{''.join(layer for layer in layers if layer)}
</svg>"""


def write_analysis_chart(
    path: str | Path,
    result: AnalysisResult,
    *,
    symbol: str | None = None,
    title: str | None = None,
    config: ChartConfig | None = None,
) -> Path:
    """Write a standalone analysis chart HTML file."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        render_analysis_chart_html(result, symbol=symbol, title=title, config=config),
        encoding="utf-8",
    )
    return output


def _chart_dimensions(cfg: ChartConfig) -> dict[str, float]:
    price_bottom = cfg.height - cfg.padding_bottom
    volume_top = price_bottom
    volume_bottom = price_bottom
    if cfg.show_volume and cfg.volume_height > 0:
        price_bottom = cfg.height - cfg.padding_bottom - cfg.volume_height - 18
        volume_top = price_bottom + 28
        volume_bottom = cfg.height - cfg.padding_bottom
    return {
        "plot_left": float(cfg.padding_left),
        "plot_right": float(cfg.width - cfg.padding_right),
        "price_top": float(cfg.padding_top),
        "price_bottom": float(price_bottom),
        "volume_top": float(volume_top),
        "volume_bottom": float(volume_bottom),
    }


def _price_domain(
    visible: pd.DataFrame,
    features: pd.DataFrame,
    signals: pd.DataFrame,
    gaps: pd.DataFrame | None,
    order_blocks: pd.DataFrame | None,
    index: pd.Index,
) -> tuple[float, float]:
    values: list[float] = []
    values.extend(_finite_values(visible[["low", "high"]]))
    for col in ("ema_20", "ema_50", "vwap", "bb_lower", "bb_upper", "active_bull_fvg_lower", "active_bear_fvg_upper"):
        if col in features:
            values.extend(_finite_values(features[col]))
    for col in ("entry_reference", "stop_reference", "target_reference"):
        if col in signals:
            values.extend(_finite_values(signals[col]))
    for table in (gaps, order_blocks):
        for row in _visible_zone_rows(table, index, max_rows=200):
            values.extend([float(row["lower"]), float(row["upper"])])

    if not values:
        return 0.0, 1.0
    low = min(values)
    high = max(values)
    if low == high:
        margin = abs(low) * 0.002 or 0.0001
    else:
        margin = (high - low) * 0.08
    return low - margin, high + margin


def _position_mapper(count: int, left: float, right: float):
    step = (right - left) / max(count - 1, 1)

    def x_at(position: float) -> float:
        return left + position * step

    return x_at


def _price_mapper(price_min: float, price_max: float, top: float, bottom: float):
    span = price_max - price_min or 1.0

    def y_at(price: float) -> float:
        return bottom - ((float(price) - price_min) / span) * (bottom - top)

    return y_at


def _render_background(
    cfg: ChartConfig,
    dims: dict[str, float],
    price_min: float,
    price_max: float,
    index: pd.Index,
) -> str:
    rows: list[str] = [
        f'<rect class="chart-bg" x="0" y="0" width="{cfg.width}" height="{cfg.height}"/>',
        f'<rect class="price-panel" x="{dims["plot_left"]:.1f}" y="{dims["price_top"]:.1f}" width="{dims["plot_right"] - dims["plot_left"]:.1f}" height="{dims["price_bottom"] - dims["price_top"]:.1f}"/>',
    ]
    if cfg.show_volume:
        rows.append(
            f'<rect class="volume-panel" x="{dims["plot_left"]:.1f}" y="{dims["volume_top"]:.1f}" width="{dims["plot_right"] - dims["plot_left"]:.1f}" height="{dims["volume_bottom"] - dims["volume_top"]:.1f}"/>'
        )
    for value in np.linspace(price_min, price_max, 6):
        y = _price_mapper(price_min, price_max, dims["price_top"], dims["price_bottom"])(float(value))
        rows.append(f'<line class="grid-line" x1="{dims["plot_left"]:.1f}" y1="{y:.1f}" x2="{dims["plot_right"]:.1f}" y2="{y:.1f}"/>')
        rows.append(f'<text class="axis-label" x="{dims["plot_right"] + 8:.1f}" y="{y + 4:.1f}">{_format_price(float(value))}</text>')

    for position, label in _time_ticks(index, max_ticks=7):
        x = _position_mapper(len(index), dims["plot_left"], dims["plot_right"])(position)
        rows.append(f'<line class="time-grid" x1="{x:.1f}" y1="{dims["price_top"]:.1f}" x2="{x:.1f}" y2="{dims["volume_bottom"]:.1f}"/>')
        rows.append(f'<text class="time-label" x="{x:.1f}" y="{cfg.height - 28:.1f}">{escape(label)}</text>')
    return "\n".join(rows)


def _render_candles(visible: pd.DataFrame, x_at, y_price, dims: dict[str, float]) -> str:
    if visible.empty:
        return ""
    step = (dims["plot_right"] - dims["plot_left"]) / max(len(visible) - 1, 1)
    body_width = max(2.0, min(12.0, step * 0.62))
    rows: list[str] = []
    for pos, (idx, row) in enumerate(visible.iterrows()):
        x = x_at(float(pos))
        open_y = y_price(float(row["open"]))
        close_y = y_price(float(row["close"]))
        high_y = y_price(float(row["high"]))
        low_y = y_price(float(row["low"]))
        top = min(open_y, close_y)
        height = max(abs(close_y - open_y), 1.2)
        side = "up" if float(row["close"]) >= float(row["open"]) else "down"
        label = _candle_title(idx, row)
        rows.append(f'<g class="candle candle-{side}"><title>{escape(label)}</title>')
        rows.append(f'<line class="candle-wick" x1="{x:.2f}" y1="{high_y:.2f}" x2="{x:.2f}" y2="{low_y:.2f}"/>')
        rows.append(
            f'<rect class="candle-body" x="{x - body_width / 2:.2f}" y="{top:.2f}" width="{body_width:.2f}" height="{height:.2f}"/>'
        )
        rows.append("</g>")
    return "\n".join(rows)


def _render_volume(visible: pd.DataFrame, x_at, dims: dict[str, float]) -> str:
    volume_col = "tick_volume" if "tick_volume" in visible else "volume" if "volume" in visible else None
    if volume_col is None:
        return ""
    volumes = pd.to_numeric(visible[volume_col], errors="coerce").fillna(0.0)
    max_volume = float(volumes.max())
    if max_volume <= 0:
        return ""
    step = (dims["plot_right"] - dims["plot_left"]) / max(len(visible) - 1, 1)
    bar_width = max(1.5, min(10.0, step * 0.58))
    rows: list[str] = []
    for pos, (_, row) in enumerate(visible.iterrows()):
        volume = float(pd.to_numeric(row.get(volume_col), errors="coerce"))
        volume = volume if np.isfinite(volume) else 0.0
        height = (volume / max_volume) * max(dims["volume_bottom"] - dims["volume_top"], 1.0)
        y = dims["volume_bottom"] - height
        side = "up" if float(row["close"]) >= float(row["open"]) else "down"
        rows.append(
            f'<rect class="volume-bar volume-{side}" x="{x_at(float(pos)) - bar_width / 2:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{max(height, 1.0):.2f}"/>'
        )
    return "\n".join(rows)


def _render_indicator_line(
    features: pd.DataFrame,
    column: str,
    index: pd.Index,
    x_at,
    y_price,
    css_class: str,
) -> str:
    if features.empty or column not in features:
        return ""
    segments = _line_segments(features[column], index, x_at, y_price)
    return "\n".join(f'<polyline class="indicator-line {css_class}" points="{points}"/>' for points in segments)


def _render_zone_table(
    table: pd.DataFrame | None,
    index: pd.Index,
    x_at,
    y_price,
    kind: str,
    max_rows: int,
) -> str:
    rows: list[str] = []
    for row in _visible_zone_rows(table, index, max_rows=max_rows):
        direction = str(row.get("direction", "neutral"))
        start_x = _timestamp_to_x(index, _first_present(row, "formed_at", "source_candle_at"), x_at)
        end_value = row.get("filled_at") if kind == "fvg" else row.get("invalidated_at")
        end_x = _timestamp_to_x(index, end_value, x_at) if _has_timestamp(end_value) else x_at(float(len(index) - 1))
        if end_x < start_x:
            start_x, end_x = end_x, start_x
        lower = float(row["lower"])
        upper = float(row["upper"])
        y1 = y_price(upper)
        y2 = y_price(lower)
        class_name = f"{kind}-zone zone-{direction}"
        label = _zone_label(kind, direction, row)
        width = max(end_x - start_x, 2.0)
        height = max(y2 - y1, 2.0)
        rows.append(f'<g class="{class_name}"><title>{escape(label)}</title>')
        rows.append(f'<rect x="{start_x:.2f}" y="{y1:.2f}" width="{width:.2f}" height="{height:.2f}"/>')
        rows.append(f'<text x="{start_x + 4:.2f}" y="{max(y1 + 13, 12):.2f}">{escape(label)}</text>')
        rows.append("</g>")
    return "\n".join(rows)


def _render_liquidity_pools(
    table: pd.DataFrame | None,
    index: pd.Index,
    x_at,
    y_price,
    dims: dict[str, float],
    max_rows: int,
) -> str:
    if table is None or table.empty or not isinstance(index, pd.DatetimeIndex):
        return ""
    visible_start = _coerce_to_index_tz(index[0], index)
    visible_end = _coerce_to_index_tz(index[-1], index)
    rows: list[str] = []
    for _, row in table.tail(max_rows).iterrows():
        first_touch = row.get("first_touch_at")
        last_touch = row.get("last_touch_at")
        if not _has_timestamp(first_touch) or not _has_timestamp(last_touch):
            continue
        start = _coerce_to_index_tz(first_touch, index)
        end = _coerce_to_index_tz(last_touch, index)
        if end < visible_start or start > visible_end:
            continue
        level = float(row["level"])
        y = y_price(level)
        kind = str(row.get("kind", "liquidity"))
        touches = int(row.get("touches", 0))
        x1 = max(_timestamp_to_x(index, start, x_at), dims["plot_left"])
        x2 = dims["plot_right"]
        label = "BSL" if kind == "buy_side" else "SSL" if kind == "sell_side" else "LIQ"
        rows.append(f'<g class="liquidity-pool pool-{escape(kind)}"><title>{escape(label)} {touches} touches @ {_format_price(level)}</title>')
        rows.append(f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}"/>')
        rows.append(f'<text x="{x2 - 62:.2f}" y="{y - 5:.2f}">{label} {touches}x</text>')
        rows.append("</g>")
    return "\n".join(rows)


def _render_structure_markers(
    visible: pd.DataFrame,
    structure: pd.DataFrame,
    index: pd.Index,
    x_at,
    y_price,
    max_rows: int,
) -> str:
    if structure.empty or "structure_event" not in structure:
        return ""
    events = structure[structure["structure_event"].notna()].tail(max_rows)
    rows: list[str] = []
    for idx, row in events.iterrows():
        if idx not in visible.index:
            continue
        pos = visible.index.get_loc(idx)
        if isinstance(pos, slice):
            continue
        direction = str(row.get("structure_direction", "neutral"))
        level = row.get("broken_level")
        candle = visible.loc[idx]
        price = float(level) if pd.notna(level) else float(candle["high"] if direction == "bullish" else candle["low"])
        x = x_at(float(pos))
        y = y_price(price)
        event = str(row.get("structure_event"))
        marker = _triangle(x, y - 12 if direction == "bullish" else y + 12, direction)
        rows.append(f'<g class="structure-marker structure-{escape(direction)}"><title>{escape(event)} {escape(direction)} @ {_format_price(price)}</title>{marker}')
        rows.append(f'<text x="{x + 6:.2f}" y="{y - 10 if direction == "bullish" else y + 22:.2f}">{escape(event)}</text></g>')
    return "\n".join(rows)


def _render_sweep_markers(
    visible: pd.DataFrame,
    features: pd.DataFrame,
    index: pd.Index,
    x_at,
    y_price,
    max_rows: int,
) -> str:
    if features.empty or "liquidity_sweep" not in features:
        return ""
    sweeps = features[features["liquidity_sweep"].notna()].tail(max_rows)
    rows: list[str] = []
    for idx, row in sweeps.iterrows():
        if idx not in visible.index:
            continue
        pos = visible.index.get_loc(idx)
        if isinstance(pos, slice):
            continue
        sweep = str(row.get("liquidity_sweep"))
        price = row.get("swept_level")
        if pd.isna(price):
            candle = visible.loc[idx]
            price = candle["high"] if sweep == "buy_side" else candle["low"]
        x = x_at(float(pos))
        y = y_price(float(price))
        rows.append(f'<g class="sweep-marker sweep-{escape(sweep)}"><title>{escape(sweep)} sweep @ {_format_price(float(price))}</title>')
        rows.append(f'<path d="M {x:.2f} {y - 7:.2f} L {x + 7:.2f} {y:.2f} L {x:.2f} {y + 7:.2f} L {x - 7:.2f} {y:.2f} Z"/>')
        rows.append("</g>")
    return "\n".join(rows)


def _render_signal_markers(
    visible: pd.DataFrame,
    signals: pd.DataFrame,
    index: pd.Index,
    x_at,
    y_price,
    dims: dict[str, float],
    max_rows: int,
) -> str:
    if signals.empty or "side" not in signals:
        return ""
    active = signals[signals["side"].isin(["long", "short"])].tail(max_rows)
    rows: list[str] = []
    for idx, row in active.iterrows():
        if idx not in visible.index:
            continue
        pos = visible.index.get_loc(idx)
        if isinstance(pos, slice):
            continue
        candle = visible.loc[idx]
        side = str(row["side"])
        x = x_at(float(pos))
        price = float(candle["low"] if side == "long" else candle["high"])
        y = y_price(price)
        offset_y = y + 18 if side == "long" else y - 18
        arrow = _signal_arrow(x, offset_y, side)
        confidence = _format_float(row.get("confidence"))
        rows.append(f'<g class="signal-marker signal-{side}"><title>{escape(side)} confidence {confidence}</title>{arrow}</g>')

    latest = active.tail(1)
    if not latest.empty:
        latest_row = latest.iloc[0]
        latest_idx = latest.index[0]
        latest_pos = visible.index.get_loc(latest_idx)
        if not isinstance(latest_pos, slice):
            x1 = x_at(float(latest_pos))
            x2 = min(dims["plot_right"], x1 + (dims["plot_right"] - dims["plot_left"]) * 0.18)
            for column, label in (
                ("entry_reference", "entry"),
                ("stop_reference", "stop"),
                ("target_reference", "target"),
            ):
                value = latest_row.get(column)
                if pd.notna(value):
                    y = y_price(float(value))
                    rows.append(f'<g class="risk-line risk-{label}"><title>{escape(label)} {_format_price(float(value))}</title>')
                    rows.append(f'<line x1="{x1:.2f}" y1="{y:.2f}" x2="{x2:.2f}" y2="{y:.2f}"/>')
                    rows.append(f'<text x="{x2 + 4:.2f}" y="{y + 4:.2f}">{label}</text></g>')
    return "\n".join(rows)


def _render_legend(dims: dict[str, float], symbol: str) -> str:
    items = [
        ("candle-up", "Up candle"),
        ("candle-down", "Down candle"),
        ("ema20", "EMA 20"),
        ("ema50", "EMA 50"),
        ("vwap", "VWAP"),
        ("fvg", "FVG"),
        ("ob", "Order block"),
        ("liq", "Liquidity"),
    ]
    x = dims["plot_left"]
    y = 18.0
    rows = [f'<text class="chart-symbol" x="{x:.1f}" y="{y:.1f}">{escape(symbol.upper())}</text>']
    cursor = x + 115
    for css_class, label in items:
        rows.append(f'<g class="legend-item legend-{css_class}"><rect x="{cursor:.1f}" y="{y - 10:.1f}" width="12" height="8"/><text x="{cursor + 17:.1f}" y="{y:.1f}">{escape(label)}</text></g>')
        cursor += 112 if len(label) < 10 else 140
    return "\n".join(rows)


def _svg_defs() -> str:
    return """<defs>
  <style>
    .chart-bg { fill: #fbfaf5; }
    .price-panel, .volume-panel { fill: #ffffff; stroke: #d9d6ca; stroke-width: 1; }
    .grid-line { stroke: #ece8da; stroke-width: 1; }
    .time-grid { stroke: #f1eee2; stroke-width: 1; }
    .axis-label, .time-label, .legend-item text, .chart-symbol { fill: #526063; font-size: 12px; letter-spacing: 0; }
    .time-label { text-anchor: middle; }
    .chart-symbol { fill: #14292c; font-size: 13px; font-weight: 700; }
    .candle-up .candle-body { fill: #12805c; stroke: #0f6148; }
    .candle-down .candle-body { fill: #b4232c; stroke: #861820; }
    .candle-wick { stroke: #334044; stroke-width: 1.2; }
    .volume-up { fill: #12805c; opacity: 0.34; }
    .volume-down { fill: #b4232c; opacity: 0.30; }
    .indicator-line { fill: none; stroke-width: 1.7; stroke-linejoin: round; stroke-linecap: round; }
    .ema20 { stroke: #2563eb; }
    .ema50 { stroke: #7c3aed; }
    .vwap { stroke: #a16207; stroke-dasharray: 5 4; }
    .fvg-zone rect { fill: #22c55e; stroke: #15803d; opacity: 0.15; }
    .fvg-zone.zone-bearish rect { fill: #ef4444; stroke: #b91c1c; opacity: 0.13; }
    .ob-zone rect { fill: #0ea5e9; stroke: #0369a1; opacity: 0.14; }
    .ob-zone.zone-bearish rect { fill: #f97316; stroke: #c2410c; opacity: 0.14; }
    .fvg-zone text, .ob-zone text, .liquidity-pool text, .structure-marker text, .risk-line text { fill: #334044; font-size: 11px; letter-spacing: 0; }
    .liquidity-pool line { stroke-width: 1.3; stroke-dasharray: 7 5; }
    .pool-buy_side line { stroke: #b4232c; }
    .pool-sell_side line { stroke: #12805c; }
    .structure-bullish path, .signal-long path { fill: #12805c; stroke: #0f6148; }
    .structure-bearish path, .signal-short path { fill: #b4232c; stroke: #861820; }
    .sweep-marker path { fill: #facc15; stroke: #a16207; stroke-width: 1; opacity: 0.92; }
    .risk-line line { stroke-width: 1.4; stroke-dasharray: 5 4; }
    .risk-entry line { stroke: #2563eb; }
    .risk-stop line { stroke: #b4232c; }
    .risk-target line { stroke: #12805c; }
    .legend-candle-up rect { fill: #12805c; }
    .legend-candle-down rect { fill: #b4232c; }
    .legend-ema20 rect { fill: #2563eb; }
    .legend-ema50 rect { fill: #7c3aed; }
    .legend-vwap rect { fill: #a16207; }
    .legend-fvg rect { fill: #22c55e; opacity: 0.45; }
    .legend-ob rect { fill: #0ea5e9; opacity: 0.45; }
    .legend-liq rect { fill: #facc15; }
  </style>
</defs>"""


def _line_segments(series: pd.Series, index: pd.Index, x_at, y_price) -> list[str]:
    numeric = pd.to_numeric(series.reindex(index), errors="coerce")
    segments: list[list[str]] = []
    current: list[str] = []
    for pos, value in enumerate(numeric):
        if pd.isna(value):
            if len(current) > 1:
                segments.append(current)
            current = []
            continue
        current.append(f"{x_at(float(pos)):.2f},{y_price(float(value)):.2f}")
    if len(current) > 1:
        segments.append(current)
    return [" ".join(segment) for segment in segments]


def _visible_zone_rows(table: pd.DataFrame | None, index: pd.Index, *, max_rows: int) -> list[pd.Series]:
    if table is None or table.empty or not isinstance(index, pd.DatetimeIndex):
        return []
    visible_start = _coerce_to_index_tz(index[0], index)
    visible_end = _coerce_to_index_tz(index[-1], index)
    out: list[pd.Series] = []
    for _, row in table.tail(max_rows * 3).iterrows():
        formed = _first_present(row, "formed_at", "source_candle_at")
        if not _has_timestamp(formed) or pd.isna(row.get("lower")) or pd.isna(row.get("upper")):
            continue
        start = _coerce_to_index_tz(formed, index)
        end_candidate = row.get("filled_at") if "filled_at" in row else row.get("invalidated_at")
        end = _coerce_to_index_tz(end_candidate, index) if _has_timestamp(end_candidate) else visible_end
        if start <= visible_end and end >= visible_start:
            out.append(row)
    return out[-max_rows:]


def _time_ticks(index: pd.Index, *, max_ticks: int) -> list[tuple[float, str]]:
    if len(index) == 0:
        return []
    positions = np.linspace(0, len(index) - 1, min(max_ticks, len(index)))
    ticks: list[tuple[float, str]] = []
    for position in positions:
        i = int(round(position))
        label = _format_timestamp(index[i])
        ticks.append((float(i), label))
    return ticks


def _timestamp_to_x(index: pd.Index, value: object, x_at) -> float:
    if not isinstance(index, pd.DatetimeIndex) or len(index) == 0 or not _has_timestamp(value):
        return x_at(0.0)
    ts = _coerce_to_index_tz(value, index)
    pos = index.searchsorted(ts)
    if pos <= 0:
        return x_at(0.0)
    if pos >= len(index):
        return x_at(float(len(index) - 1))
    before = index[pos - 1]
    after = index[pos]
    before_value = pd.Timestamp(before).value
    after_value = pd.Timestamp(after).value
    frac = 0.0 if after_value == before_value else (ts.value - before_value) / (after_value - before_value)
    return x_at(float(pos - 1 + frac))


def _coerce_to_index_tz(value: object, index: pd.DatetimeIndex) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if index.tz is not None:
        return ts.tz_localize(index.tz) if ts.tzinfo is None else ts.tz_convert(index.tz)
    return ts.tz_convert("UTC").tz_localize(None) if ts.tzinfo is not None else ts


def _has_timestamp(value: object) -> bool:
    if value is None:
        return False
    try:
        return not pd.isna(value)
    except (TypeError, ValueError):
        return True


def _tail_reindex(frame: pd.DataFrame | None, index: pd.Index) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(index=index)
    return frame.reindex(index)


def _first_present(row: pd.Series, *columns: str) -> object:
    for column in columns:
        value = row.get(column)
        if _has_timestamp(value):
            return value
    return None


def _finite_values(values: pd.DataFrame | pd.Series) -> list[float]:
    array = pd.to_numeric(values.stack() if isinstance(values, pd.DataFrame) else values, errors="coerce")
    return [float(value) for value in array[np.isfinite(array)]]


def _format_price(value: float) -> str:
    abs_value = abs(value)
    if abs_value >= 100:
        return f"{value:.2f}"
    if abs_value >= 10:
        return f"{value:.3f}"
    return f"{value:.5f}"


def _format_float(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.3f}"
    return str(value)


def _format_timestamp(value: object) -> str:
    if isinstance(value, pd.Timestamp):
        return value.strftime("%m-%d %H:%M") if value.hour or value.minute else value.strftime("%Y-%m-%d")
    return str(value)


def _candle_title(idx: object, row: pd.Series) -> str:
    return (
        f"{_format_timestamp(idx)} "
        f"O {_format_price(float(row['open']))} "
        f"H {_format_price(float(row['high']))} "
        f"L {_format_price(float(row['low']))} "
        f"C {_format_price(float(row['close']))}"
    )


def _zone_label(kind: str, direction: str, row: pd.Series) -> str:
    prefix = "FVG" if kind == "fvg" else "OB"
    side = "Bull" if direction == "bullish" else "Bear" if direction == "bearish" else direction.title()
    return f"{side} {prefix} {_format_price(float(row['lower']))}-{_format_price(float(row['upper']))}"


def _triangle(x: float, y: float, direction: str) -> str:
    if direction == "bullish":
        points = [(x, y - 7), (x + 7, y + 7), (x - 7, y + 7)]
    else:
        points = [(x, y + 7), (x + 7, y - 7), (x - 7, y - 7)]
    path = " ".join(f"{px:.2f},{py:.2f}" for px, py in points)
    return f'<path d="M {path} Z"/>'


def _signal_arrow(x: float, y: float, side: str) -> str:
    if side == "long":
        return f'<path d="M {x:.2f} {y - 12:.2f} L {x + 8:.2f} {y + 2:.2f} L {x + 3:.2f} {y + 2:.2f} L {x + 3:.2f} {y + 13:.2f} L {x - 3:.2f} {y + 13:.2f} L {x - 3:.2f} {y + 2:.2f} L {x - 8:.2f} {y + 2:.2f} Z"/>'
    return f'<path d="M {x:.2f} {y + 12:.2f} L {x + 8:.2f} {y - 2:.2f} L {x + 3:.2f} {y - 2:.2f} L {x + 3:.2f} {y - 13:.2f} L {x - 3:.2f} {y - 13:.2f} L {x - 3:.2f} {y - 2:.2f} L {x - 8:.2f} {y - 2:.2f} Z"/>'


def _summary_table(values: dict[str, object]) -> str:
    rows = "".join(
        f"<tr><th>{escape(str(key))}</th><td>{escape(_format_float(value))}</td></tr>"
        for key, value in values.items()
    )
    return f"<table>{rows}</table>"


def _infer_symbol(features: pd.DataFrame, default: str) -> str:
    if not features.empty and "symbol" in features:
        values = features["symbol"].dropna()
        if not values.empty:
            return str(values.iloc[-1])
    return default


def _empty_svg(cfg: ChartConfig, message: str) -> str:
    return f"""<svg class="smc-ta-chart candlestick-chart" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {cfg.width} {cfg.height}" role="img" aria-label="{escape(message)}">
<rect x="0" y="0" width="{cfg.width}" height="{cfg.height}" fill="#fbfaf5"/>
<text x="{cfg.width / 2:.1f}" y="{cfg.height / 2:.1f}" text-anchor="middle" fill="#526063" font-size="16">{escape(message)}</text>
</svg>"""
