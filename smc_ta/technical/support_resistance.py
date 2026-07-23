"""Support and resistance helpers."""

from __future__ import annotations

import pandas as pd

from smc_ta.validation import normalize_ohlcv


def pivot_points_standard(df: pd.DataFrame, timeframe: str = "1D") -> pd.DataFrame:
    """Standard floor-trader pivot levels projected to the next period."""

    data = normalize_ohlcv(df, copy=False)
    if not isinstance(data.index, pd.DatetimeIndex):
        raise TypeError("pivot_points_standard requires a DateTimeIndex")
    grouped = data.resample(timeframe).agg({"high": "max", "low": "min", "close": "last"})
    pivot = (grouped["high"] + grouped["low"] + grouped["close"]) / 3.0
    levels = pd.DataFrame(index=grouped.index)
    levels["pivot"] = pivot
    levels["r1"] = 2 * pivot - grouped["low"]
    levels["s1"] = 2 * pivot - grouped["high"]
    levels["r2"] = pivot + (grouped["high"] - grouped["low"])
    levels["s2"] = pivot - (grouped["high"] - grouped["low"])
    levels["r3"] = grouped["high"] + 2 * (pivot - grouped["low"])
    levels["s3"] = grouped["low"] - 2 * (grouped["high"] - pivot)
    shifted = levels.shift(1)
    return shifted.reindex(data.index, method="ffill")


def fibonacci_retracements(high: float, low: float) -> dict[str, float]:
    """Return common Fibonacci retracement levels for a dealing range."""

    if high <= low:
        raise ValueError("high must be greater than low")
    diff = high - low
    return {
        "0.0": high,
        "0.236": high - 0.236 * diff,
        "0.382": high - 0.382 * diff,
        "0.5": high - 0.5 * diff,
        "0.618": high - 0.618 * diff,
        "0.786": high - 0.786 * diff,
        "1.0": low,
    }


def rolling_support_resistance(df: pd.DataFrame, period: int = 50) -> pd.DataFrame:
    """Rolling high/low support and resistance."""

    data = normalize_ohlcv(df, copy=False)
    resistance = data["high"].rolling(period, min_periods=period).max()
    support = data["low"].rolling(period, min_periods=period).min()
    midpoint = (support + resistance) / 2.0
    return pd.DataFrame(
        {
            "rolling_support": support,
            "rolling_resistance": resistance,
            "rolling_midpoint": midpoint,
        },
        index=data.index,
    )

