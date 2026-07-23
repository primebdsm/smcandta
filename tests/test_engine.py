from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta import ConfluenceConfig, analyze_forex, build_smc_ta_features


def make_engine_candles(n: int = 180) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    wave = np.sin(np.arange(n) / 6) * 0.0012
    drift = np.arange(n) * 0.00002
    close = pd.Series(1.0900 + wave + drift, index=index)
    open_ = close.shift(1).fillna(close.iloc[0] - 0.0001)
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 30),
            "spread": 0.0001,
        },
        index=index,
    )


def test_feature_builder_and_analyze_forex_contract() -> None:
    candles = make_engine_candles()
    config = ConfluenceConfig(min_signal_score=5)
    features = build_smc_ta_features(candles, symbol="EURUSD", config=config)
    result = analyze_forex(candles, symbol="EURUSD", config=config)

    assert len(features) == len(candles)
    assert len(result.features) == len(candles)
    assert len(result.signals) == len(candles)
    assert {"side", "confidence", "long_score", "short_score", "reasons"}.issubset(result.signals.columns)
    assert {"structure_trend", "active_bull_fvg_distance", "spread_pips"}.issubset(result.features.columns)
    assert result.signals["side"].isin(["long", "short", "flat"]).all()

