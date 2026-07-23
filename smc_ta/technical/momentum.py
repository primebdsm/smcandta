"""Momentum indicators."""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.validation import normalize_ohlcv, safe_divide, typical_price


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder smoothing."""

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    average_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    average_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = safe_divide(average_gain, average_loss)
    out = 100 - (100 / (1 + rs))
    return out.clip(lower=0, upper=100)


def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
    smooth_k: int = 3,
) -> pd.DataFrame:
    """Stochastic oscillator."""

    data = normalize_ohlcv(df, copy=False)
    lowest_low = data["low"].rolling(k_period, min_periods=k_period).min()
    highest_high = data["high"].rolling(k_period, min_periods=k_period).max()
    fast_k = 100 * safe_divide(data["close"] - lowest_low, highest_high - lowest_low)
    slow_k = fast_k.rolling(smooth_k, min_periods=smooth_k).mean()
    slow_d = slow_k.rolling(d_period, min_periods=d_period).mean()
    return pd.DataFrame({"stoch_k": slow_k, "stoch_d": slow_d}, index=data.index)


def cci(df: pd.DataFrame, period: int = 20, constant: float = 0.015) -> pd.Series:
    """Commodity Channel Index."""

    data = normalize_ohlcv(df, copy=False)
    tp = typical_price(data)
    ma = tp.rolling(period, min_periods=period).mean()
    mean_deviation = tp.rolling(period, min_periods=period).apply(
        lambda values: float(np.mean(np.abs(values - np.mean(values)))),
        raw=True,
    )
    return safe_divide(tp - ma, constant * mean_deviation)


def roc(close: pd.Series, period: int = 12) -> pd.Series:
    """Rate of Change."""

    return 100 * safe_divide(close - close.shift(period), close.shift(period))


def williams_r(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Williams %R."""

    data = normalize_ohlcv(df, copy=False)
    highest_high = data["high"].rolling(period, min_periods=period).max()
    lowest_low = data["low"].rolling(period, min_periods=period).min()
    return -100 * safe_divide(highest_high - data["close"], highest_high - lowest_low)

