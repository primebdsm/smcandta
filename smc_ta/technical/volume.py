"""Volume and tick-volume proxy indicators.

Spot Forex has no centralized exchange volume. These functions use `volume`
or `tick_volume` as supplied by the broker/data vendor.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.validation import normalize_ohlcv, safe_divide, typical_price


def _volume_series(df: pd.DataFrame) -> pd.Series:
    data = normalize_ohlcv(df, copy=False)
    if "volume" in data.columns:
        return data["volume"]
    if "tick_volume" in data.columns:
        return data["tick_volume"]
    return pd.Series(1.0, index=data.index)


def obv(df: pd.DataFrame) -> pd.Series:
    """On Balance Volume using volume or tick_volume."""

    data = normalize_ohlcv(df, copy=False)
    volume = _volume_series(data)
    direction = np.sign(data["close"].diff()).fillna(0.0)
    return (direction * volume).cumsum()


def money_flow_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Money Flow Index using volume or tick_volume."""

    data = normalize_ohlcv(df, copy=False)
    volume = _volume_series(data)
    tp = typical_price(data)
    flow = tp * volume
    positive = flow.where(tp > tp.shift(1), 0.0)
    negative = flow.where(tp < tp.shift(1), 0.0)
    positive_sum = positive.rolling(period, min_periods=period).sum()
    negative_sum = negative.rolling(period, min_periods=period).sum()
    ratio = safe_divide(positive_sum, negative_sum)
    return (100 - (100 / (1 + ratio))).clip(lower=0, upper=100)


def vwap(df: pd.DataFrame, period: int | None = None) -> pd.Series:
    """VWAP or rolling VWAP using broker volume/tick_volume."""

    data = normalize_ohlcv(df, copy=False)
    volume = _volume_series(data)
    tp = typical_price(data)
    weighted = tp * volume
    if period is None:
        return safe_divide(weighted.cumsum(), volume.cumsum())
    return safe_divide(
        weighted.rolling(period, min_periods=period).sum(),
        volume.rolling(period, min_periods=period).sum(),
    )

