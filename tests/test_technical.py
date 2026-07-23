from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.technical import build_technical_snapshot
from smc_ta.technical.momentum import rsi
from smc_ta.technical.volatility import atr, bollinger_bands


def make_candles(n: int = 160) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    base = 1.1000 + np.arange(n) * 0.00003 + np.sin(np.arange(n) / 7) * 0.001
    open_ = pd.Series(base, index=index).shift(1).fillna(base[0])
    close = pd.Series(base, index=index)
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.00035
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.00035
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 20),
            "spread": 0.00012,
        },
        index=index,
    )


def test_core_technical_indicators_are_aligned() -> None:
    candles = make_candles()
    snapshot = build_technical_snapshot(candles)

    assert len(snapshot) == len(candles)
    assert {"ema_20", "ema_50", "rsi_14", "atr_14", "bb_upper"}.issubset(snapshot.columns)
    assert atr(candles, 14).dropna().ge(0).all()
    assert rsi(candles["close"], 14).dropna().between(0, 100).all()


def test_bollinger_bands_shape() -> None:
    candles = make_candles()
    bands = bollinger_bands(candles["close"], period=20)

    assert list(bands.columns) == ["bb_lower", "bb_middle", "bb_upper", "bb_width", "bb_percent_b"]
    complete = bands.dropna()
    assert (complete["bb_upper"] >= complete["bb_middle"]).all()
    assert (complete["bb_middle"] >= complete["bb_lower"]).all()

