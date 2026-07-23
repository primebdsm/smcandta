# Chart Visualization

The repository includes a dependency-free static chart renderer for Forex SMC/TA analysis.

Main APIs:

- `render_analysis_chart_svg(...)`
- `render_analysis_chart_html(...)`
- `write_analysis_chart(...)`
- `ChartConfig`

## Quick Usage

```python
from smc_ta import ChartConfig, analyze_forex, write_analysis_chart
from smc_ta.data import load_csv_candles

candles = load_csv_candles("EURUSD_M15.csv")
result = analyze_forex(candles, symbol="EURUSD")

write_analysis_chart(
    "analysis_chart.html",
    result,
    symbol="EURUSD",
    config=ChartConfig(visible_bars=160),
)
```

Command-line example:

```bash
python examples/render_analysis_chart.py EURUSD_M15.csv --symbol EURUSD --output analysis_chart.html
```

## Rendered Layers

The chart uses the real `AnalysisResult` object returned by `analyze_forex`.

Rendered price-action layers:

- Candles from normalized OHLCV data
- Tick-volume or volume histogram
- EMA 20, EMA 50, and VWAP overlays when available
- Fair Value Gap zones from `result.fair_value_gaps`
- Order Block zones from `result.order_blocks`
- Buy-side and sell-side liquidity pools from `result.liquidity_pools`
- Liquidity sweep markers from `result.features`
- BOS/CHoCH markers from `result.market_structure`
- Long/short signal markers from `result.signals`
- Latest entry, stop, and target reference lines when a signal exists

## Bot Integration

The chart renderer does not calculate new signals. It visualizes already computed SMC/TA output.

Typical workflow:

1. Load or download candles.
2. Run `analyze_forex`.
3. Send `result.signals.iloc[-1]` into risk/news/execution logic.
4. Write a chart only for journal review, dashboard snapshots, alerts, or debugging.

This keeps trading logic deterministic and separates visual reporting from execution.

## Configuration

```python
ChartConfig(
    width=1280,
    height=760,
    visible_bars=160,
    show_volume=True,
    show_ema=True,
    show_vwap=True,
    show_smc_zones=True,
    show_liquidity=True,
    show_structure=True,
    show_signals=True,
)
```

The renderer writes portable HTML/SVG and does not require Plotly, Matplotlib, JavaScript, or a web server.
