from __future__ import annotations

import numpy as np
import pandas as pd

from smc_ta.data import DataQualityConfig, load_and_validate_csv_candles, validate_candle_quality


def make_candles(n: int = 20) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = pd.Series(1.1000 + np.arange(n) * 0.0001, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0002
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0002
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100,
            "spread": 0.0001,
        },
        index=index,
    )


def issue_kinds(report) -> set[str]:
    return {issue.kind for issue in report.issues}


def test_clean_candles_pass_quality_validation() -> None:
    report = validate_candle_quality(make_candles(), config=DataQualityConfig(timeframe="M15"))

    assert report.ok
    assert report.summary() == "data_quality_ok"
    assert report.inferred_timeframe == pd.Timedelta("15min")
    assert report.to_frame().empty


def test_missing_required_columns_block() -> None:
    candles = make_candles().drop(columns=["close"])
    report = validate_candle_quality(candles)

    assert not report.ok
    assert "missing_required_columns" in report.blocking_reasons


def test_invalid_ohlc_and_nan_values_are_reported() -> None:
    candles = make_candles()
    candles.iloc[2, candles.columns.get_loc("high")] = candles.iloc[2]["low"] - 0.001
    candles.iloc[3, candles.columns.get_loc("close")] = np.nan
    report = validate_candle_quality(candles, config=DataQualityConfig(timeframe="M15"))

    assert not report.ok
    assert {"invalid_ohlc_relationship", "nan_ohlc_values"}.issubset(issue_kinds(report))


def test_duplicate_non_monotonic_and_missing_candles_are_reported() -> None:
    candles = make_candles()
    duplicated = pd.concat([candles.iloc[:5], candles.iloc[[4]], candles.iloc[8:12]])
    shuffled = duplicated.iloc[[0, 1, 2, 5, 3, 4, 6, 7, 8]]
    report = validate_candle_quality(shuffled, config=DataQualityConfig(timeframe="M15"))

    assert not report.ok
    assert {"duplicate_timestamps", "non_monotonic_index", "missing_candles"}.issubset(issue_kinds(report))


def test_spread_and_range_anomalies_are_warnings_by_default() -> None:
    candles = make_candles(20)
    candles.iloc[10, candles.columns.get_loc("spread")] = 0.0015
    candles.iloc[11, candles.columns.get_loc("high")] = candles.iloc[11]["high"] + 0.01
    report = validate_candle_quality(
        candles,
        config=DataQualityConfig(timeframe="M15", max_spread_pips=5.0, max_range_median_multiple=5.0),
    )

    assert report.ok
    assert {"spread_above_limit", "spread_spike", "range_spike"}.issubset(issue_kinds(report))
    assert all(issue.severity == "warning" for issue in report.issues)


def test_weekend_candles_are_warning_and_weekend_gaps_can_be_ignored() -> None:
    friday = pd.date_range("2024-01-05 21:45", periods=2, freq="15min", tz="UTC")
    monday = pd.date_range("2024-01-08 00:00", periods=2, freq="15min", tz="UTC")
    weekend = pd.DatetimeIndex([pd.Timestamp("2024-01-06 12:00", tz="UTC")])
    index = friday.append(weekend).append(monday)
    close = pd.Series(1.1000 + np.arange(len(index)) * 0.0001, index=index)
    candles = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.0002,
            "low": close - 0.0002,
            "close": close,
        },
        index=index,
    )
    report = validate_candle_quality(candles, config=DataQualityConfig(timeframe="M15", ignore_weekend_gaps=True))

    assert report.ok
    assert issue_kinds(report) == {"weekend_candles"}


def test_load_and_validate_csv_candles_sets_time_index(tmp_path) -> None:
    candles = make_candles(5)
    csv_path = tmp_path / "EURUSD_M15.csv"
    candles.reset_index(names="time").to_csv(csv_path, index=False)

    raw, report = load_and_validate_csv_candles(
        csv_path,
        config=DataQualityConfig(symbol="EURUSD", timeframe="M15"),
    )

    assert isinstance(raw.index, pd.DatetimeIndex)
    assert len(raw) == 5
    assert report.ok

