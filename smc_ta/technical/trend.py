"""Trend indicators."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from smc_ta.validation import normalize_ohlcv, safe_divide


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""

    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""

    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted moving average."""

    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period, min_periods=period).apply(
        lambda values: float(np.dot(values, weights) / weights.sum()),
        raw=True,
    )


def hma(series: pd.Series, period: int) -> pd.Series:
    """Hull moving average."""

    half = max(1, period // 2)
    root = max(1, int(math.sqrt(period)))
    return wma(2 * wma(series, half) - wma(series, period), root)


def macd(
    close: pd.Series,
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """Moving Average Convergence Divergence."""

    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {
            "macd": macd_line,
            "macd_signal": signal_line,
            "macd_histogram": histogram,
        },
        index=close.index,
    )


def _wilder(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _true_range_values(df: pd.DataFrame) -> pd.Series:
    previous_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Average Directional Index with +DI and -DI."""

    data = normalize_ohlcv(df, copy=False)
    high_diff = data["high"].diff()
    low_diff = -data["low"].diff()

    plus_dm = pd.Series(
        np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0),
        index=data.index,
    )
    minus_dm = pd.Series(
        np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0),
        index=data.index,
    )

    tr = _true_range_values(data)
    atr_values = _wilder(tr, period)
    plus_di = 100 * safe_divide(_wilder(plus_dm, period), atr_values)
    minus_di = 100 * safe_divide(_wilder(minus_dm, period), atr_values)
    dx = 100 * safe_divide((plus_di - minus_di).abs(), plus_di + minus_di)
    adx_line = _wilder(dx, period)

    return pd.DataFrame(
        {
            "plus_di": plus_di,
            "minus_di": minus_di,
            "adx": adx_line,
        },
        index=data.index,
    )


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
    """Supertrend direction and trailing bands."""

    data = normalize_ohlcv(df, copy=False)
    from smc_ta.technical.volatility import atr

    atr_values = atr(data, period)
    hl2 = (data["high"] + data["low"]) / 2.0
    basic_upper = hl2 + multiplier * atr_values
    basic_lower = hl2 - multiplier * atr_values

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    direction = pd.Series(index=data.index, dtype="object")
    trend = pd.Series(index=data.index, dtype="float64")

    for i in range(len(data)):
        if i == 0 or pd.isna(atr_values.iloc[i]):
            direction.iloc[i] = "neutral"
            trend.iloc[i] = np.nan
            continue

        prev_i = i - 1
        if basic_upper.iloc[i] < final_upper.iloc[prev_i] or data["close"].iloc[prev_i] > final_upper.iloc[prev_i]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[prev_i]

        if basic_lower.iloc[i] > final_lower.iloc[prev_i] or data["close"].iloc[prev_i] < final_lower.iloc[prev_i]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[prev_i]

        previous_direction = direction.iloc[prev_i]
        if previous_direction in ("neutral", "bearish") and data["close"].iloc[i] > final_upper.iloc[prev_i]:
            direction.iloc[i] = "bullish"
        elif previous_direction in ("neutral", "bullish") and data["close"].iloc[i] < final_lower.iloc[prev_i]:
            direction.iloc[i] = "bearish"
        else:
            direction.iloc[i] = previous_direction

        trend.iloc[i] = final_lower.iloc[i] if direction.iloc[i] == "bullish" else final_upper.iloc[i]

    return pd.DataFrame(
        {
            "supertrend": trend,
            "supertrend_direction": direction,
            "supertrend_upper": final_upper,
            "supertrend_lower": final_lower,
        },
        index=data.index,
    )

