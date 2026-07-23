"""Candlestick pattern helpers."""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.validation import normalize_ohlcv, safe_divide


def candle_body(df: pd.DataFrame) -> pd.Series:
    data = normalize_ohlcv(df, copy=False)
    return (data["close"] - data["open"]).abs()


def upper_wick(df: pd.DataFrame) -> pd.Series:
    data = normalize_ohlcv(df, copy=False)
    return data["high"] - data[["open", "close"]].max(axis=1)


def lower_wick(df: pd.DataFrame) -> pd.Series:
    data = normalize_ohlcv(df, copy=False)
    return data[["open", "close"]].min(axis=1) - data["low"]


def doji(df: pd.DataFrame, body_to_range: float = 0.1) -> pd.Series:
    """Return true when the candle body is small relative to range."""

    data = normalize_ohlcv(df, copy=False)
    body_ratio = safe_divide(candle_body(data), data["high"] - data["low"])
    return body_ratio <= body_to_range


def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Bullish engulfing candle pattern."""

    data = normalize_ohlcv(df, copy=False)
    previous_bearish = data["close"].shift(1) < data["open"].shift(1)
    current_bullish = data["close"] > data["open"]
    engulfs = (data["open"] <= data["close"].shift(1)) & (data["close"] >= data["open"].shift(1))
    return previous_bearish & current_bullish & engulfs


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    """Bearish engulfing candle pattern."""

    data = normalize_ohlcv(df, copy=False)
    previous_bullish = data["close"].shift(1) > data["open"].shift(1)
    current_bearish = data["close"] < data["open"]
    engulfs = (data["open"] >= data["close"].shift(1)) & (data["close"] <= data["open"].shift(1))
    return previous_bullish & current_bearish & engulfs


def pin_bar(df: pd.DataFrame, wick_body_ratio: float = 2.5) -> pd.Series:
    """Pin bar with one dominant wick."""

    data = normalize_ohlcv(df, copy=False)
    body = candle_body(data).replace(0, np.nan)
    upper = upper_wick(data)
    lower = lower_wick(data)
    return (upper >= wick_body_ratio * body) | (lower >= wick_body_ratio * body)


def inside_bar(df: pd.DataFrame) -> pd.Series:
    """Inside bar pattern."""

    data = normalize_ohlcv(df, copy=False)
    return (data["high"] < data["high"].shift(1)) & (data["low"] > data["low"].shift(1))
