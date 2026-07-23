"""Volatility indicators."""

from __future__ import annotations

import pandas as pd

from smc_ta.technical.trend import ema
from smc_ta.validation import normalize_ohlcv, safe_divide


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range."""

    data = normalize_ohlcv(df, copy=False)
    previous_close = data["close"].shift(1)
    ranges = pd.concat(
        [
            data["high"] - data["low"],
            (data["high"] - previous_close).abs(),
            (data["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range using Wilder smoothing."""

    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bollinger_bands(close: pd.Series, period: int = 20, stddev: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands."""

    middle = close.rolling(period, min_periods=period).mean()
    sigma = close.rolling(period, min_periods=period).std(ddof=0)
    upper = middle + stddev * sigma
    lower = middle - stddev * sigma
    width = safe_divide(upper - lower, middle)
    percent_b = safe_divide(close - lower, upper - lower)
    return pd.DataFrame(
        {
            "bb_lower": lower,
            "bb_middle": middle,
            "bb_upper": upper,
            "bb_width": width,
            "bb_percent_b": percent_b,
        },
        index=close.index,
    )


def keltner_channels(
    df: pd.DataFrame,
    period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """Keltner Channels."""

    data = normalize_ohlcv(df, copy=False)
    middle = ema(data["close"], period)
    atr_values = atr(data, atr_period)
    upper = middle + multiplier * atr_values
    lower = middle - multiplier * atr_values
    return pd.DataFrame(
        {
            "keltner_lower": lower,
            "keltner_middle": middle,
            "keltner_upper": upper,
        },
        index=data.index,
    )


def donchian_channels(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Donchian Channels."""

    data = normalize_ohlcv(df, copy=False)
    upper = data["high"].rolling(period, min_periods=period).max()
    lower = data["low"].rolling(period, min_periods=period).min()
    middle = (upper + lower) / 2.0
    return pd.DataFrame(
        {
            "donchian_lower": lower,
            "donchian_middle": middle,
            "donchian_upper": upper,
        },
        index=data.index,
    )


def average_daily_range(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average Daily Range for time-indexed Forex candles."""

    data = normalize_ohlcv(df, copy=False)
    if not isinstance(data.index, pd.DatetimeIndex):
        raise TypeError("average_daily_range requires a DateTimeIndex")
    daily = data.resample("1D").agg({"high": "max", "low": "min"})
    adr = (daily["high"] - daily["low"]).rolling(period, min_periods=period).mean()
    return adr.reindex(data.index, method="ffill")

