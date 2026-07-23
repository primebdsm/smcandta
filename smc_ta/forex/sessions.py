"""Forex session helpers.

Times are approximate UTC windows. Adjust them in production if your broker,
data vendor, or daylight-saving convention differs.
"""

from __future__ import annotations

import pandas as pd

from smc_ta.validation import normalize_ohlcv

SESSION_WINDOWS_UTC = {
    "sydney": (21, 6),
    "tokyo": (0, 9),
    "london": (7, 16),
    "new_york": (12, 21),
}

KILL_ZONES_UTC = {
    "london_kill_zone": (7, 10),
    "new_york_kill_zone": (12, 15),
}


def _in_hour_window(hours: pd.Index, start: int, end: int) -> pd.Series:
    if start < end:
        values = (hours >= start) & (hours < end)
    else:
        values = (hours >= start) | (hours < end)
    return pd.Series(values, index=hours.index if hasattr(hours, "index") else None)


def session_labels(index: pd.Index) -> pd.DataFrame:
    """Return session boolean columns for a DateTimeIndex."""

    if not isinstance(index, pd.DatetimeIndex):
        raise TypeError("session labels require a pandas DateTimeIndex")

    dt_index = index
    if dt_index.tz is not None:
        dt_index = dt_index.tz_convert("UTC")

    hours = pd.Series(dt_index.hour, index=index)
    out = pd.DataFrame(index=index)
    for name, (start, end) in SESSION_WINDOWS_UTC.items():
        out[f"session_{name}"] = _in_hour_window(hours, start, end).to_numpy()
    for name, (start, end) in KILL_ZONES_UTC.items():
        out[name] = _in_hour_window(hours, start, end).to_numpy()
    out["session_overlap_london_new_york"] = out["session_london"] & out["session_new_york"]
    return out


def add_session_features(df: pd.DataFrame) -> pd.DataFrame:
    """Append session features to an OHLCV DataFrame."""

    data = normalize_ohlcv(df, copy=False)
    return data.join(session_labels(data.index))

