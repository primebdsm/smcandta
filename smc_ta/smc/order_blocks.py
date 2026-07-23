"""Order Block detection."""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.smc.structure import market_structure
from smc_ta.technical.volatility import atr
from smc_ta.validation import normalize_ohlcv


def _candidate_zone(row: pd.Series, direction: str, zone_mode: str) -> tuple[float, float]:
    if zone_mode == "full":
        return float(row["low"]), float(row["high"])
    if zone_mode != "body":
        raise ValueError("zone_mode must be 'full' or 'body'")
    if direction == "bullish":
        return float(row["low"]), float(max(row["open"], row["close"]))
    return float(min(row["open"], row["close"])), float(row["high"])


def detect_order_blocks(
    df: pd.DataFrame,
    structure: pd.DataFrame | None = None,
    *,
    search_lookback: int = 10,
    min_displacement_atr: float = 1.0,
    atr_period: int = 14,
    zone_mode: str = "full",
) -> pd.DataFrame:
    """Detect order blocks before BOS/CHoCH displacement events."""

    data = normalize_ohlcv(df, copy=False)
    structure_table = (
        market_structure(data) if structure is None else structure.reindex(data.index)
    )
    atr_values = atr(data, atr_period)
    records: list[dict[str, object]] = []

    events = structure_table[structure_table["structure_event"].notna()]
    for event_idx, event in events.iterrows():
        i = data.index.get_loc(event_idx)
        if isinstance(i, slice):
            continue
        direction = str(event["structure_direction"])
        start = max(0, int(i) - search_lookback)
        candidate_slice = data.iloc[start:int(i)]
        if candidate_slice.empty:
            continue

        if direction == "bullish":
            candidates = candidate_slice[candidate_slice["close"] < candidate_slice["open"]]
        else:
            candidates = candidate_slice[candidate_slice["close"] > candidate_slice["open"]]
        if candidates.empty:
            continue

        candidate_time = candidates.index[-1]
        candidate = candidates.iloc[-1]
        current_atr = atr_values.loc[event_idx]
        displacement = abs(float(data.loc[event_idx, "close"]) - float(candidate["close"]))
        if pd.isna(current_atr) or displacement < float(current_atr) * min_displacement_atr:
            continue

        lower, upper = _candidate_zone(candidate, direction, zone_mode)
        mitigated_at = pd.NaT
        invalidated_at = pd.NaT

        for j in range(int(i) + 1, len(data)):
            row = data.iloc[j]
            if direction == "bullish":
                if pd.isna(mitigated_at) and row["low"] <= upper:
                    mitigated_at = data.index[j]
                if row["close"] < lower:
                    invalidated_at = data.index[j]
                    break
            else:
                if pd.isna(mitigated_at) and row["high"] >= lower:
                    mitigated_at = data.index[j]
                if row["close"] > upper:
                    invalidated_at = data.index[j]
                    break

        records.append(
            {
                "order_block_id": f"ob_{len(records) + 1}",
                "formed_at": event_idx,
                "source_candle_at": candidate_time,
                "event": event["structure_event"],
                "direction": direction,
                "lower": lower,
                "upper": upper,
                "midpoint": (lower + upper) / 2.0,
                "displacement": displacement,
                "mitigated_at": mitigated_at,
                "invalidated_at": invalidated_at,
                "active": pd.isna(invalidated_at),
            }
        )

    return pd.DataFrame.from_records(records)


def active_order_block_features(df: pd.DataFrame, order_blocks: pd.DataFrame | None = None) -> pd.DataFrame:
    """Create candle-aligned nearest-active-order-block features."""

    data = normalize_ohlcv(df, copy=False)
    blocks = detect_order_blocks(data) if order_blocks is None else order_blocks
    out = pd.DataFrame(index=data.index)
    columns = [
        "active_bull_ob_lower",
        "active_bull_ob_upper",
        "active_bull_ob_distance",
        "active_bear_ob_lower",
        "active_bear_ob_upper",
        "active_bear_ob_distance",
    ]
    for col in columns:
        out[col] = np.nan

    if blocks.empty:
        return out

    for idx in data.index:
        close = data.at[idx, "close"]
        formed = blocks[blocks["formed_at"] <= idx]
        active = formed[
            formed["invalidated_at"].isna() | (formed["invalidated_at"] > idx)
        ]
        if active.empty:
            continue
        for direction, prefix in (("bullish", "active_bull_ob"), ("bearish", "active_bear_ob")):
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

