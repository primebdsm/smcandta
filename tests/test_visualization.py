from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta import AnalysisResult, ChartConfig, render_analysis_chart_html, render_analysis_chart_svg, write_analysis_chart


def make_chart_result() -> AnalysisResult:
    index = pd.date_range("2024-01-01", periods=12, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + np.sin(np.arange(len(index)) / 2) * 0.0006 + np.arange(len(index)) * 0.00008, index=index)
    open_ = close.shift(1).fillna(close.iloc[0] - 0.0001)
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.00025
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.00025
    candles = pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + np.arange(len(index)) * 5,
            "spread": 0.0001,
        },
        index=index,
    )
    features = candles.copy()
    features["ema_20"] = close.ewm(span=3, adjust=False).mean()
    features["ema_50"] = close.ewm(span=5, adjust=False).mean()
    features["vwap"] = close.expanding().mean()
    features["liquidity_sweep"] = pd.Series(index=index, dtype="object")
    features["swept_level"] = np.nan
    features.loc[index[7], "liquidity_sweep"] = "sell_side"
    features.loc[index[7], "swept_level"] = float(low.iloc[5])
    features["structure_trend"] = "bullish"
    features["pd_zone"] = "discount"
    features["spread_pips"] = 1.0
    features["symbol"] = "EURUSD"

    signals = pd.DataFrame(index=index)
    signals["side"] = "flat"
    signals["confidence"] = 0.0
    signals["long_score"] = 0
    signals["short_score"] = 0
    signals["entry_reference"] = np.nan
    signals["stop_reference"] = np.nan
    signals["target_reference"] = np.nan
    signals["reasons"] = ""
    signals.loc[index[-1], ["side", "confidence", "long_score", "entry_reference", "stop_reference", "target_reference", "reasons"]] = [
        "long",
        0.82,
        8,
        float(close.iloc[-1]),
        float(low.iloc[6]),
        float(close.iloc[-1] + 0.001),
        "test_signal",
    ]

    structure = pd.DataFrame(index=index)
    structure["structure_event"] = pd.Series(index=index, dtype="object")
    structure["structure_direction"] = pd.Series(index=index, dtype="object")
    structure["broken_level"] = np.nan
    structure.loc[index[6], ["structure_event", "structure_direction", "broken_level"]] = [
        "BOS",
        "bullish",
        float(high.iloc[4]),
    ]

    fair_value_gaps = pd.DataFrame(
        [
            {
                "gap_id": "fvg_1",
                "formed_at": index[3],
                "direction": "bullish",
                "lower": float(low.iloc[3]),
                "upper": float(low.iloc[3] + 0.00025),
                "filled_at": pd.NaT,
            }
        ]
    )
    order_blocks = pd.DataFrame(
        [
            {
                "order_block_id": "ob_1",
                "formed_at": index[4],
                "source_candle_at": index[2],
                "direction": "bearish",
                "lower": float(high.iloc[4] - 0.00035),
                "upper": float(high.iloc[4]),
                "invalidated_at": pd.NaT,
            }
        ]
    )
    liquidity_pools = pd.DataFrame(
        [
            {
                "pool_id": "liq_1",
                "kind": "buy_side",
                "level": float(high.iloc[5]),
                "lower": float(high.iloc[5] - 0.00005),
                "upper": float(high.iloc[5] + 0.00005),
                "touches": 2,
                "first_touch_at": index[2],
                "last_touch_at": index[8],
            }
        ]
    )
    return AnalysisResult(
        candles=candles,
        features=features,
        signals=signals,
        market_structure=structure,
        fair_value_gaps=fair_value_gaps,
        order_blocks=order_blocks,
        liquidity_pools=liquidity_pools,
    )


def test_render_analysis_chart_svg_contains_smc_ta_layers() -> None:
    result = make_chart_result()

    svg = render_analysis_chart_svg(
        result.candles,
        features=result.features,
        signals=result.signals,
        market_structure=result.market_structure,
        fair_value_gaps=result.fair_value_gaps,
        order_blocks=result.order_blocks,
        liquidity_pools=result.liquidity_pools,
        symbol="EURUSD",
        config=ChartConfig(width=900, height=560, visible_bars=12),
    )

    assert 'class="smc-ta-chart candlestick-chart"' in svg
    assert svg.count('class="candle-body"') == len(result.candles)
    assert "fvg-zone zone-bullish" in svg
    assert "ob-zone zone-bearish" in svg
    assert "liquidity-pool pool-buy_side" in svg
    assert "sweep-marker sweep-sell_side" in svg
    assert "structure-marker structure-bullish" in svg
    assert "signal-marker signal-long" in svg
    assert "risk-line risk-entry" in svg
    assert "EMA 20" in svg
    assert "VWAP" in svg


def test_render_and_write_analysis_chart_html(tmp_path) -> None:
    result = make_chart_result()

    html = render_analysis_chart_html(result, symbol="EURUSD", config=ChartConfig(visible_bars=8))
    output = write_analysis_chart(tmp_path / "chart.html", result, symbol="EURUSD", config=ChartConfig(visible_bars=8))

    assert "EURUSD SMC TA Chart" in html
    assert "Current Signal" in html
    assert "test_signal" in html
    assert output.exists()
    assert "candlestick-chart" in output.read_text(encoding="utf-8")
