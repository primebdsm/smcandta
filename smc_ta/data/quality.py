"""Data quality validation for Forex OHLCV candles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from smc_ta.forex.pairs import infer_pip_size, spread_to_pips
from smc_ta.validation import OPTIONAL_COLUMNS, REQUIRED_OHLC, normalize_column_name

IssueSeverity = Literal["info", "warning", "blocking"]


@dataclass(frozen=True)
class DataQualityConfig:
    """Controls candle quality checks."""

    timeframe: str | pd.Timedelta | None = None
    symbol: str = "EURUSD"
    missing_gap_tolerance: float = 1.5
    ignore_weekend_gaps: bool = True
    allow_weekend_candles: bool = False
    max_spread_pips: float | None = 5.0
    spread_median_multiple: float | None = 5.0
    max_range_median_multiple: float | None = 8.0
    min_rows: int = 1
    sample_limit: int = 10
    invalid_ohlc_severity: IssueSeverity = "blocking"
    missing_candles_severity: IssueSeverity = "blocking"
    duplicate_timestamp_severity: IssueSeverity = "blocking"
    abnormal_spread_severity: IssueSeverity = "warning"
    abnormal_range_severity: IssueSeverity = "warning"
    weekend_candle_severity: IssueSeverity = "warning"


@dataclass(frozen=True)
class DataQualityIssue:
    """One candle data quality issue."""

    kind: str
    severity: IssueSeverity
    message: str
    count: int = 0
    sample: tuple[object, ...] = ()
    details: dict[str, object] = field(default_factory=dict)

    @property
    def blocking(self) -> bool:
        return self.severity == "blocking"


@dataclass(frozen=True)
class DataQualityReport:
    """Full data quality report."""

    row_count: int
    start: pd.Timestamp | None
    end: pd.Timestamp | None
    inferred_timeframe: pd.Timedelta | None
    issues: tuple[DataQualityIssue, ...]

    @property
    def ok(self) -> bool:
        return not any(issue.blocking for issue in self.issues)

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return tuple(issue.kind for issue in self.issues if issue.blocking)

    def to_frame(self) -> pd.DataFrame:
        """Return issues as a DataFrame."""

        return pd.DataFrame(
            [
                {
                    "kind": issue.kind,
                    "severity": issue.severity,
                    "message": issue.message,
                    "count": issue.count,
                    "sample": list(issue.sample),
                    "details": issue.details,
                }
                for issue in self.issues
            ]
        )

    def summary(self) -> str:
        if self.ok:
            return "data_quality_ok"
        return ";".join(self.blocking_reasons)


def validate_candle_quality(
    df: pd.DataFrame,
    *,
    config: DataQualityConfig | None = None,
) -> DataQualityReport:
    """Validate Forex OHLCV candle quality without mutating or fixing data."""

    cfg = config or DataQualityConfig()
    issues: list[DataQualityIssue] = []
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")

    data = _normalize_headers_only(df)
    row_count = len(data)
    start, end = _index_bounds(data.index)
    if row_count < cfg.min_rows:
        issues.append(
            DataQualityIssue(
                kind="not_enough_rows",
                severity="blocking",
                message=f"expected at least {cfg.min_rows} rows, got {row_count}",
                count=row_count,
            )
        )

    missing_columns = [column for column in REQUIRED_OHLC if column not in data.columns]
    if missing_columns:
        issues.append(
            DataQualityIssue(
                kind="missing_required_columns",
                severity="blocking",
                message=f"missing required OHLC columns: {missing_columns}",
                count=len(missing_columns),
                sample=tuple(missing_columns),
            )
        )
        return DataQualityReport(row_count, start, end, None, tuple(issues))

    numeric = data.copy()
    for column in REQUIRED_OHLC + OPTIONAL_COLUMNS:
        if column in numeric.columns:
            numeric[column] = pd.to_numeric(numeric[column], errors="coerce")

    issues.extend(_nan_issues(numeric, cfg))
    issues.extend(_ohlc_issues(numeric, cfg))
    issues.extend(_index_issues(numeric, cfg))
    timeframe = _resolve_timeframe(numeric.index, cfg.timeframe)
    issues.extend(_gap_issues(numeric, timeframe, cfg))
    issues.extend(_weekend_candle_issues(numeric, cfg))
    issues.extend(_spread_issues(numeric, cfg))
    issues.extend(_range_issues(numeric, cfg))
    return DataQualityReport(row_count, start, end, timeframe, tuple(issues))


def load_and_validate_csv_candles(
    path: str | Path,
    *,
    time_column: str = "time",
    timezone: str | None = "UTC",
    config: DataQualityConfig | None = None,
) -> tuple[pd.DataFrame, DataQualityReport]:
    """Read a CSV, set its time index if present, and return the quality report."""

    raw = pd.read_csv(path)
    lowered = {str(column).lower(): column for column in raw.columns}
    normalized_time_col = lowered.get(time_column.lower(), time_column)
    if normalized_time_col in raw.columns:
        raw[normalized_time_col] = pd.to_datetime(raw[normalized_time_col], utc=timezone == "UTC")
        raw = raw.set_index(normalized_time_col)
    if isinstance(raw.index, pd.DatetimeIndex) and timezone and raw.index.tz is None:
        raw.index = raw.index.tz_localize(timezone)
    return raw, validate_candle_quality(raw, config=config)


def _normalize_headers_only(df: pd.DataFrame) -> pd.DataFrame:
    return df.rename(columns={column: normalize_column_name(column) for column in df.columns}).copy()


def _index_bounds(index: pd.Index) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if len(index) == 0 or not isinstance(index, pd.DatetimeIndex):
        return None, None
    return pd.Timestamp(index.min()), pd.Timestamp(index.max())


def _sample(values, limit: int) -> tuple[object, ...]:
    return tuple(list(values)[:limit])


def _nan_issues(data: pd.DataFrame, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    nan_counts = data[[column for column in REQUIRED_OHLC if column in data.columns]].isna().sum()
    bad = nan_counts[nan_counts > 0]
    if not bad.empty:
        issues.append(
            DataQualityIssue(
                kind="nan_ohlc_values",
                severity="blocking",
                message="OHLC columns contain NaN or non-numeric values",
                count=int(bad.sum()),
                details={str(column): int(count) for column, count in bad.items()},
            )
        )
    return issues


def _ohlc_issues(data: pd.DataFrame, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    valid_rows = data.dropna(subset=list(REQUIRED_OHLC))
    if valid_rows.empty:
        return []
    high_is_valid = valid_rows["high"] >= valid_rows[["open", "close", "low"]].max(axis=1)
    low_is_valid = valid_rows["low"] <= valid_rows[["open", "close", "high"]].min(axis=1)
    invalid = ~(high_is_valid & low_is_valid)
    if not invalid.any():
        return []
    bad_index = valid_rows.index[invalid]
    return [
        DataQualityIssue(
            kind="invalid_ohlc_relationship",
            severity=cfg.invalid_ohlc_severity,
            message="high/low do not contain open, close, and each other",
            count=int(invalid.sum()),
            sample=_sample(bad_index, cfg.sample_limit),
        )
    ]


def _index_issues(data: pd.DataFrame, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    issues: list[DataQualityIssue] = []
    if not isinstance(data.index, pd.DatetimeIndex):
        return [
            DataQualityIssue(
                kind="non_datetime_index",
                severity="blocking",
                message="candle index must be a pandas DateTimeIndex",
                count=len(data),
            )
        ]
    if not data.index.is_monotonic_increasing:
        issues.append(
            DataQualityIssue(
                kind="non_monotonic_index",
                severity="blocking",
                message="candle timestamps must be sorted ascending",
                count=len(data),
            )
        )
    duplicates = data.index.duplicated(keep=False)
    if duplicates.any():
        issues.append(
            DataQualityIssue(
                kind="duplicate_timestamps",
                severity=cfg.duplicate_timestamp_severity,
                message="duplicate candle timestamps found",
                count=int(duplicates.sum()),
                sample=_sample(data.index[duplicates], cfg.sample_limit),
            )
        )
    return issues


def _resolve_timeframe(index: pd.Index, timeframe: str | pd.Timedelta | None) -> pd.Timedelta | None:
    if timeframe is not None:
        if isinstance(timeframe, pd.Timedelta):
            return timeframe
        return pd.Timedelta(_timeframe_alias(timeframe))
    if not isinstance(index, pd.DatetimeIndex) or len(index) < 2:
        return None
    unique_index = index[~index.duplicated()].sort_values()
    diffs = unique_index.to_series().diff().dropna()
    if diffs.empty:
        return None
    return pd.Timedelta(diffs.mode().iloc[0])


def _timeframe_alias(value: str) -> str:
    aliases = {
        "M1": "1min",
        "M5": "5min",
        "M15": "15min",
        "M30": "30min",
        "H1": "1h",
        "H4": "4h",
        "D": "1D",
        "D1": "1D",
    }
    return aliases.get(value.upper(), value)


def _gap_issues(data: pd.DataFrame, timeframe: pd.Timedelta | None, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    if timeframe is None or not isinstance(data.index, pd.DatetimeIndex) or len(data.index) < 2:
        return []
    unique_index = data.index[~data.index.duplicated()].sort_values()
    diffs = unique_index.to_series().diff().dropna()
    threshold = timeframe * cfg.missing_gap_tolerance
    gap_mask = diffs > threshold
    if cfg.ignore_weekend_gaps:
        gap_mask = gap_mask & ~pd.Series(
            [
                _gap_is_weekend(previous, current)
                for previous, current in zip(unique_index[:-1], unique_index[1:])
            ],
            index=diffs.index,
        )
    if not gap_mask.any():
        return []
    gap_rows = []
    for current_time, delta in diffs[gap_mask].items():
        previous_time = unique_index[unique_index.get_loc(current_time) - 1]
        missing = max(0, int(round(delta / timeframe)) - 1)
        gap_rows.append((previous_time, current_time, missing))
    return [
        DataQualityIssue(
            kind="missing_candles",
            severity=cfg.missing_candles_severity,
            message="timestamp gaps larger than expected timeframe found",
            count=sum(row[2] for row in gap_rows),
            sample=_sample(gap_rows, cfg.sample_limit),
            details={"timeframe": str(timeframe), "gaps": len(gap_rows)},
        )
    ]


def _gap_is_weekend(previous: pd.Timestamp, current: pd.Timestamp) -> bool:
    span = pd.date_range(previous.normalize(), current.normalize(), freq="1D", tz=previous.tz)
    return any(day.weekday() >= 5 for day in span)


def _weekend_candle_issues(data: pd.DataFrame, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    if cfg.allow_weekend_candles or not isinstance(data.index, pd.DatetimeIndex):
        return []
    weekend = data.index.weekday >= 5
    if not weekend.any():
        return []
    return [
        DataQualityIssue(
            kind="weekend_candles",
            severity=cfg.weekend_candle_severity,
            message="candles exist during weekend market-closure window",
            count=int(weekend.sum()),
            sample=_sample(data.index[weekend], cfg.sample_limit),
        )
    ]


def _spread_issues(data: pd.DataFrame, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    if "spread" not in data.columns:
        return []
    spread = data["spread"].dropna()
    if spread.empty:
        return []
    spread_pips = spread_to_pips(spread, pip_size=infer_pip_size(cfg.symbol))
    issues: list[DataQualityIssue] = []
    if cfg.max_spread_pips is not None:
        high = spread_pips > cfg.max_spread_pips
        if high.any():
            issues.append(
                DataQualityIssue(
                    kind="spread_above_limit",
                    severity=cfg.abnormal_spread_severity,
                    message=f"spread exceeds {cfg.max_spread_pips} pips",
                    count=int(high.sum()),
                    sample=_sample(spread_pips.index[high], cfg.sample_limit),
                    details={"max_spread_pips": cfg.max_spread_pips},
                )
            )
    if cfg.spread_median_multiple is not None and len(spread_pips) >= 5:
        median = float(spread_pips.median())
        if median > 0:
            abnormal = spread_pips > median * cfg.spread_median_multiple
            if abnormal.any():
                issues.append(
                    DataQualityIssue(
                        kind="spread_spike",
                        severity=cfg.abnormal_spread_severity,
                        message="spread is abnormal relative to median spread",
                        count=int(abnormal.sum()),
                        sample=_sample(spread_pips.index[abnormal], cfg.sample_limit),
                        details={"median_spread_pips": median, "multiple": cfg.spread_median_multiple},
                    )
                )
    return issues


def _range_issues(data: pd.DataFrame, cfg: DataQualityConfig) -> list[DataQualityIssue]:
    if cfg.max_range_median_multiple is None or data[list(REQUIRED_OHLC)].isna().any().any():
        return []
    candle_range = (data["high"] - data["low"]).replace([np.inf, -np.inf], np.nan).dropna()
    if len(candle_range) < 5:
        return []
    median = float(candle_range.median())
    if median <= 0:
        return []
    abnormal = candle_range > median * cfg.max_range_median_multiple
    if not abnormal.any():
        return []
    return [
        DataQualityIssue(
            kind="range_spike",
            severity=cfg.abnormal_range_severity,
            message="candle range is abnormal relative to median range",
            count=int(abnormal.sum()),
            sample=_sample(candle_range.index[abnormal], cfg.sample_limit),
            details={"median_range": median, "multiple": cfg.max_range_median_multiple},
        )
    ]
