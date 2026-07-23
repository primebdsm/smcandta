"""Market-data source abstractions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from smc_ta.validation import normalize_ohlcv


class CandleDataSource(Protocol):
    """Protocol for historical/live candle providers."""

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """Return normalized OHLCV candles."""


def load_csv_candles(
    path: str | Path,
    *,
    time_column: str = "time",
    timezone: str | None = "UTC",
) -> pd.DataFrame:
    """Load broker/export CSV candles and normalize them."""

    file_path = Path(path)
    raw = pd.read_csv(file_path)
    normalized_time_col = time_column
    if normalized_time_col not in raw.columns:
        lowered = {str(col).lower(): col for col in raw.columns}
        normalized_time_col = lowered.get(time_column.lower(), time_column)
    if normalized_time_col in raw.columns:
        raw[normalized_time_col] = pd.to_datetime(raw[normalized_time_col], utc=timezone == "UTC")
        raw = raw.set_index(normalized_time_col)
    out = normalize_ohlcv(raw)
    if isinstance(out.index, pd.DatetimeIndex) and timezone and out.index.tz is None:
        out.index = out.index.tz_localize(timezone)
    return out


@dataclass(frozen=True)
class CsvCandleDataSource:
    """Directory-backed candle source.

    Files are resolved as `{symbol}_{timeframe}.csv` unless an explicit mapping
    is provided.
    """

    root: str | Path
    file_map: dict[tuple[str, str], str | Path] | None = None
    time_column: str = "time"

    def _path_for(self, symbol: str, timeframe: str) -> Path:
        key = (symbol.upper(), timeframe)
        if self.file_map and key in self.file_map:
            return Path(self.file_map[key])
        return Path(self.root) / f"{symbol.upper()}_{timeframe}.csv"

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        *,
        start: pd.Timestamp | None = None,
        end: pd.Timestamp | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        candles = load_csv_candles(self._path_for(symbol, timeframe), time_column=self.time_column)
        if start is not None:
            candles = candles[candles.index >= pd.Timestamp(start)]
        if end is not None:
            candles = candles[candles.index <= pd.Timestamp(end)]
        if limit is not None:
            candles = candles.tail(limit)
        return candles

