"""Liquidity pools, sweeps, and premium/discount zones."""

from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.smc.structure import swing_points
from smc_ta.technical.volatility import atr
from smc_ta.validation import normalize_ohlcv


def _tolerance_at(
    atr_values: pd.Series,
    i: int,
    *,
    tolerance: float | None,
    atr_multiple: float | None,
) -> float:
    resolved = tolerance if tolerance is not None else 0.0
    if atr_multiple is not None and not pd.isna(atr_values.iloc[i]):
        resolved = max(resolved, float(atr_values.iloc[i]) * atr_multiple)
    return resolved


def equal_highs_lows(
    df: pd.DataFrame,
    *,
    left: int = 3,
    right: int = 3,
    tolerance: float | None = None,
    atr_multiple: float | None = 0.1,
    min_touches: int = 2,
) -> pd.DataFrame:
    """Group similar swing highs/lows into liquidity pools."""

    data = normalize_ohlcv(df, copy=False)
    swings = swing_points(data, left=left, right=right)
    atr_values = atr(data, 14)
    pools: list[dict[str, object]] = []

    def add_level(kind: str, idx, price: float, tol: float) -> None:
        for pool in pools:
            if pool["kind"] == kind and abs(float(pool["level"]) - price) <= max(float(pool["tolerance"]), tol):
                touches = int(pool["touches"]) + 1
                pool["level"] = (float(pool["level"]) * int(pool["touches"]) + price) / touches
                pool["touches"] = touches
                pool["last_touch_at"] = idx
                pool["lower"] = min(float(pool["lower"]), price - tol)
                pool["upper"] = max(float(pool["upper"]), price + tol)
                pool["tolerance"] = max(float(pool["tolerance"]), tol)
                return
        pools.append(
            {
                "pool_id": f"liq_{len(pools) + 1}",
                "kind": kind,
                "level": price,
                "lower": price - tol,
                "upper": price + tol,
                "tolerance": tol,
                "touches": 1,
                "first_touch_at": idx,
                "last_touch_at": idx,
            }
        )

    for i, idx in enumerate(data.index):
        tol = _tolerance_at(atr_values, i, tolerance=tolerance, atr_multiple=atr_multiple)
        if bool(swings["swing_high"].iloc[i]):
            add_level("buy_side", idx, float(swings["swing_high_price"].iloc[i]), tol)
        if bool(swings["swing_low"].iloc[i]):
            add_level("sell_side", idx, float(swings["swing_low_price"].iloc[i]), tol)

    table = pd.DataFrame.from_records(pools)
    if table.empty:
        return table
    return table[table["touches"] >= min_touches].reset_index(drop=True)


def liquidity_sweeps(
    df: pd.DataFrame,
    *,
    left: int = 3,
    right: int = 3,
    buffer: float = 0.0,
) -> pd.DataFrame:
    """Detect stop-run style sweeps of confirmed swing highs/lows."""

    data = normalize_ohlcv(df, copy=False)
    swings = swing_points(data, left=left, right=right)
    out = pd.DataFrame(index=data.index)
    out["liquidity_sweep"] = pd.Series(index=data.index, dtype="object")
    out["swept_level"] = np.nan

    last_high: float | None = None
    last_low: float | None = None

    for i, idx in enumerate(data.index):
        if bool(swings["confirmed_swing_high"].iloc[i]) and not pd.isna(swings["confirmed_swing_high_price"].iloc[i]):
            last_high = float(swings["confirmed_swing_high_price"].iloc[i])
        if bool(swings["confirmed_swing_low"].iloc[i]) and not pd.isna(swings["confirmed_swing_low_price"].iloc[i]):
            last_low = float(swings["confirmed_swing_low_price"].iloc[i])

        if last_high is not None and data["high"].iloc[i] > last_high + buffer and data["close"].iloc[i] < last_high:
            out.at[idx, "liquidity_sweep"] = "buy_side"
            out.at[idx, "swept_level"] = last_high
        elif last_low is not None and data["low"].iloc[i] < last_low - buffer and data["close"].iloc[i] > last_low:
            out.at[idx, "liquidity_sweep"] = "sell_side"
            out.at[idx, "swept_level"] = last_low

    return out


def premium_discount_zones(df: pd.DataFrame, lookback: int = 100) -> pd.DataFrame:
    """Rolling dealing range with premium, discount, and equilibrium labels."""

    data = normalize_ohlcv(df, copy=False)
    high = data["high"].rolling(lookback, min_periods=max(2, lookback // 2)).max()
    low = data["low"].rolling(lookback, min_periods=max(2, lookback // 2)).min()
    equilibrium = (high + low) / 2.0
    zone = pd.Series("equilibrium", index=data.index, dtype="object")
    zone[data["close"] > equilibrium] = "premium"
    zone[data["close"] < equilibrium] = "discount"
    return pd.DataFrame(
        {
            "dealing_range_low": low,
            "dealing_range_high": high,
            "equilibrium": equilibrium,
            "pd_zone": zone,
        },
        index=data.index,
    )

