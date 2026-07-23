"""Input normalization and validation."""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
import pandas as pd

REQUIRED_OHLC = ("open", "high", "low", "close")
OPTIONAL_COLUMNS = ("volume", "tick_volume", "spread")

DEFAULT_COLUMN_MAP = {
    "time": "time",
    "date": "time",
    "datetime": "time",
    "timestamp": "time",
    "open": "open",
    "o": "open",
    "high": "high",
    "h": "high",
    "low": "low",
    "l": "low",
    "close": "close",
    "c": "close",
    "volume": "volume",
    "vol": "volume",
    "tick_volume": "tick_volume",
    "tickvolume": "tick_volume",
    "tick volume": "tick_volume",
    "spread": "spread",
}


def normalize_column_name(name: object) -> str:
    """Convert broker/export column names into normalized lowercase names."""

    clean = str(name).strip().replace("-", "_").replace(" ", "_").lower()
    return DEFAULT_COLUMN_MAP.get(clean, clean)


def normalize_ohlcv(
    df: pd.DataFrame,
    column_map: Mapping[str, str] | None = None,
    *,
    copy: bool = True,
    require_volume: bool = False,
    sort_index: bool = True,
) -> pd.DataFrame:
    """Return a validated OHLCV frame with normalized column names.

    Parameters
    ----------
    df:
        Source candles.
    column_map:
        Optional explicit rename map applied before automatic normalization.
    copy:
        Whether to copy the input before normalization.
    require_volume:
        Set to true for tools that require either `volume` or `tick_volume`.
    sort_index:
        Sort the index ascending if it is not already monotonic.
    """

    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    if df.empty:
        raise ValueError("df must contain at least one candle")

    out = df.copy() if copy else df
    if column_map:
        out = out.rename(columns=column_map)
    out = out.rename(columns={col: normalize_column_name(col) for col in out.columns})

    missing = [col for col in REQUIRED_OHLC if col not in out.columns]
    if missing:
        raise ValueError(f"missing required OHLC columns: {missing}")

    for col in REQUIRED_OHLC + OPTIONAL_COLUMNS:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if out[list(REQUIRED_OHLC)].isna().any().any():
        raise ValueError("OHLC columns contain NaN or non-numeric values")

    if require_volume and "volume" not in out.columns and "tick_volume" not in out.columns:
        raise ValueError("volume or tick_volume is required")

    high_is_valid = out["high"] >= out[["open", "close", "low"]].max(axis=1)
    low_is_valid = out["low"] <= out[["open", "close", "high"]].min(axis=1)
    if not bool(high_is_valid.all() and low_is_valid.all()):
        bad = out.index[~(high_is_valid & low_is_valid)][:5].tolist()
        raise ValueError(f"invalid OHLC relationship at rows: {bad}")

    if sort_index and not out.index.is_monotonic_increasing:
        out = out.sort_index()

    return out


def typical_price(df: pd.DataFrame) -> pd.Series:
    """Return candle typical price."""

    data = normalize_ohlcv(df, copy=False)
    return (data["high"] + data["low"] + data["close"]) / 3.0


def safe_divide(numerator: pd.Series, denominator: pd.Series | float) -> pd.Series:
    """Divide while converting zero denominators to NaN."""

    denom = denominator.replace(0, np.nan) if isinstance(denominator, pd.Series) else denominator
    if not isinstance(denom, pd.Series) and denom == 0:
        denom = np.nan
    return numerator / denom

