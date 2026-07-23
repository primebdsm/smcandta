"""Market structure and swing logic."""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.validation import normalize_ohlcv


def swing_points(
    df: pd.DataFrame,
    *,
    left: int = 3,
    right: int = 3,
    min_move: float | None = None,
    use_close: bool = False,
) -> pd.DataFrame:
    """Detect swing highs and lows.

    `swing_high` and `swing_low` mark the pivot candle. `confirmed_*` columns
    mark when the pivot becomes known after `right` candles.
    """

    if left < 1 or right < 1:
        raise ValueError("left and right must be >= 1")

    data = normalize_ohlcv(df, copy=False)
    high_source = data["close"] if use_close else data["high"]
    low_source = data["close"] if use_close else data["low"]
    out = pd.DataFrame(index=data.index)
    out["swing_high"] = False
    out["swing_low"] = False
    out["swing_high_price"] = np.nan
    out["swing_low_price"] = np.nan

    last_accepted_price: float | None = None
    for i in range(left, len(data) - right):
        high_window = high_source.iloc[i - left : i + right + 1]
        low_window = low_source.iloc[i - left : i + right + 1]
        current_high = high_source.iloc[i]
        current_low = low_source.iloc[i]

        is_high = current_high == high_window.max() and (high_window == current_high).sum() == 1
        is_low = current_low == low_window.min() and (low_window == current_low).sum() == 1

        if min_move is not None and last_accepted_price is not None:
            if is_high and abs(current_high - last_accepted_price) < min_move:
                is_high = False
            if is_low and abs(current_low - last_accepted_price) < min_move:
                is_low = False

        if is_high:
            out.iloc[i, out.columns.get_loc("swing_high")] = True
            out.iloc[i, out.columns.get_loc("swing_high_price")] = float(current_high)
            last_accepted_price = float(current_high)
        if is_low:
            out.iloc[i, out.columns.get_loc("swing_low")] = True
            out.iloc[i, out.columns.get_loc("swing_low_price")] = float(current_low)
            last_accepted_price = float(current_low)

    out["confirmed_swing_high"] = out["swing_high"].shift(right, fill_value=False)
    out["confirmed_swing_low"] = out["swing_low"].shift(right, fill_value=False)
    out["confirmed_swing_high_price"] = out["swing_high_price"].shift(right)
    out["confirmed_swing_low_price"] = out["swing_low_price"].shift(right)
    return out


def market_structure(
    df: pd.DataFrame,
    *,
    left: int = 3,
    right: int = 3,
    break_by: str = "close",
    min_break: float = 0.0,
    min_swing_move: float | None = None,
) -> pd.DataFrame:
    """Detect BOS/CHoCH events from confirmed swing levels."""

    if break_by not in {"close", "wick"}:
        raise ValueError("break_by must be 'close' or 'wick'")

    data = normalize_ohlcv(df, copy=False)
    swings = swing_points(data, left=left, right=right, min_move=min_swing_move)
    out = swings.copy()
    out["structure_event"] = pd.Series(index=data.index, dtype="object")
    out["structure_direction"] = pd.Series(index=data.index, dtype="object")
    out["structure_trend"] = "neutral"
    out["broken_level"] = np.nan

    last_high: float | None = None
    last_low: float | None = None
    last_broken_high: float | None = None
    last_broken_low: float | None = None
    trend = "neutral"

    for i, idx in enumerate(data.index):
        if bool(out["confirmed_swing_high"].iloc[i]) and not pd.isna(out["confirmed_swing_high_price"].iloc[i]):
            last_high = float(out["confirmed_swing_high_price"].iloc[i])
        if bool(out["confirmed_swing_low"].iloc[i]) and not pd.isna(out["confirmed_swing_low_price"].iloc[i]):
            last_low = float(out["confirmed_swing_low_price"].iloc[i])

        high_break_value = data["close"].iloc[i] if break_by == "close" else data["high"].iloc[i]
        low_break_value = data["close"].iloc[i] if break_by == "close" else data["low"].iloc[i]

        bullish_break = (
            last_high is not None
            and high_break_value > last_high + min_break
            and last_broken_high != last_high
        )
        bearish_break = (
            last_low is not None
            and low_break_value < last_low - min_break
            and last_broken_low != last_low
        )

        event: str | None = None
        direction: str | None = None
        broken_level: float | None = None

        if bullish_break and bearish_break:
            bullish_break = data["close"].iloc[i] >= data["open"].iloc[i]
            bearish_break = not bullish_break

        if bullish_break:
            event = "CHoCH" if trend == "bearish" else "BOS"
            direction = "bullish"
            broken_level = last_high
            last_broken_high = last_high
            trend = "bullish"
        elif bearish_break:
            event = "CHoCH" if trend == "bullish" else "BOS"
            direction = "bearish"
            broken_level = last_low
            last_broken_low = last_low
            trend = "bearish"

        if event is not None:
            out.at[idx, "structure_event"] = event
            out.at[idx, "structure_direction"] = direction
            out.at[idx, "broken_level"] = broken_level

        out.at[idx, "structure_trend"] = trend

    return out

