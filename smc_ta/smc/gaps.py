"""Fair Value Gap detection."""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.technical.volatility import atr
from smc_ta.validation import normalize_ohlcv


def fair_value_gaps(
    df: pd.DataFrame,
    *,
    min_size: float = 0.0,
    min_atr_multiple: float | None = None,
    atr_period: int = 14,
) -> pd.DataFrame:
    """Detect three-candle Fair Value Gaps.

    Bullish FVG: candle i-2 high is below candle i low.
    Bearish FVG: candle i-2 low is above candle i high.
    """

    data = normalize_ohlcv(df, copy=False)
    atr_values = atr(data, atr_period)
    records: list[dict[str, object]] = []

    for i in range(2, len(data)):
        direction: str | None = None
        lower: float | None = None
        upper: float | None = None

        if data["high"].iloc[i - 2] < data["low"].iloc[i]:
            direction = "bullish"
            lower = float(data["high"].iloc[i - 2])
            upper = float(data["low"].iloc[i])
        elif data["low"].iloc[i - 2] > data["high"].iloc[i]:
            direction = "bearish"
            lower = float(data["high"].iloc[i])
            upper = float(data["low"].iloc[i - 2])

        if direction is None or lower is None or upper is None:
            continue

        size = upper - lower
        if size < min_size:
            continue
        if min_atr_multiple is not None:
            current_atr = atr_values.iloc[i]
            if pd.isna(current_atr) or size < current_atr * min_atr_multiple:
                continue

        mitigated_at = pd.NaT
        filled_at = pd.NaT
        max_fill_ratio = 0.0
        for j in range(i + 1, len(data)):
            if direction == "bullish":
                if data["low"].iloc[j] <= upper:
                    mitigated_at = data.index[j]
                    fill_depth = upper - max(float(data["low"].iloc[j]), lower)
                    max_fill_ratio = max(max_fill_ratio, float(np.clip(fill_depth / size, 0, 1)))
                if data["low"].iloc[j] <= lower:
                    filled_at = data.index[j]
                    max_fill_ratio = 1.0
                    break
            else:
                if data["high"].iloc[j] >= lower:
                    mitigated_at = data.index[j]
                    fill_depth = min(float(data["high"].iloc[j]), upper) - lower
                    max_fill_ratio = max(max_fill_ratio, float(np.clip(fill_depth / size, 0, 1)))
                if data["high"].iloc[j] >= upper:
                    filled_at = data.index[j]
                    max_fill_ratio = 1.0
                    break

        records.append(
            {
                "gap_id": f"fvg_{len(records) + 1}",
                "formed_at": data.index[i],
                "direction": direction,
                "lower": lower,
                "upper": upper,
                "midpoint": (lower + upper) / 2.0,
                "size": size,
                "mitigated_at": mitigated_at,
                "filled_at": filled_at,
                "max_fill_ratio": max_fill_ratio,
                "active": pd.isna(filled_at),
            }
        )

    return pd.DataFrame.from_records(records)


def active_fvg_features(df: pd.DataFrame, gaps: pd.DataFrame | None = None) -> pd.DataFrame:
    """Create candle-aligned nearest-active-FVG features."""

    data = normalize_ohlcv(df, copy=False)
    gap_table = fair_value_gaps(data) if gaps is None else gaps
    out = pd.DataFrame(index=data.index)
    columns = [
        "active_bull_fvg_lower",
        "active_bull_fvg_upper",
        "active_bull_fvg_distance",
        "active_bear_fvg_lower",
        "active_bear_fvg_upper",
        "active_bear_fvg_distance",
    ]
    for col in columns:
        out[col] = np.nan

    if gap_table.empty:
        return out

    for i, idx in enumerate(data.index):
        close = data["close"].iloc[i]
        formed = gap_table[gap_table["formed_at"] <= idx]
        if formed.empty:
            continue
        active = formed[
            formed["filled_at"].isna() | (formed["filled_at"] > idx)
        ]
        if active.empty:
            continue

        for direction, prefix in (("bullish", "active_bull_fvg"), ("bearish", "active_bear_fvg")):
            subset = active[active["direction"] == direction].copy()
            if subset.empty:
                continue
            subset["distance"] = np.where(
                close < subset["lower"],
                subset["lower"] - close,
                np.where(close > subset["upper"], close - subset["upper"], 0.0),
            )
            nearest = subset.sort_values(["distance", "formed_at"]).iloc[0]
            out.at[idx, f"{prefix}_lower"] = nearest["lower"]
            out.at[idx, f"{prefix}_upper"] = nearest["upper"]
            out.at[idx, f"{prefix}_distance"] = nearest["distance"]

    return out

