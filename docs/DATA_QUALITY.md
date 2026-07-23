# Data Quality Validator

Use the data quality validator before analysis, backtesting, walk-forward testing, or live demo decisions.

## What It Checks

- Missing required OHLC columns
- NaN or non-numeric OHLC values
- Invalid OHLC relationships
- Non-DateTime index
- Non-monotonic timestamps
- Duplicate timestamps
- Missing candles based on the expected timeframe
- Weekend candles
- Spread above a configured pip limit
- Spread spikes relative to median spread
- Candle range spikes relative to median range

## Usage

```python
from smc_ta.data import DataQualityConfig, validate_candle_quality

report = validate_candle_quality(
    candles,
    config=DataQualityConfig(symbol="EURUSD", timeframe="M15"),
)

if not report.ok:
    print(report.to_frame())
    raise RuntimeError(report.summary())
```

## CSV CLI

```bash
python3 examples/validate_data.py EURUSD_M15.csv --symbol EURUSD --timeframe M15
```

## Important

The validator does not repair data. It reports problems so the data source, broker export, downloader, or preprocessing pipeline can be fixed deliberately.

