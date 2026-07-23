# Codex Guide

This repository is a Forex analysis toolkit. It combines two analysis families:

- Smart Money Concept instruments: market structure, BOS/CHoCH, liquidity pools, liquidity sweeps, fair value gaps, order blocks, premium/discount zones, and session context.
- Technical analysis instruments: trend, momentum, volatility, support/resistance, tick-volume proxies, and candlestick patterns.

The code is built for deterministic Python bot integration. Every detector should accept a pandas OHLCV DataFrame and return either a DataFrame aligned to the original candles or an event table with timestamps and price zones.

## Data Contract

Required candle columns are:

- `open`
- `high`
- `low`
- `close`

Optional columns are:

- `volume` or `tick_volume`
- `spread`

Use lowercase column names inside the package. If broker data has names like `Open`, `High`, or `tickVolume`, normalize it through `smc_ta.validation.normalize_ohlcv`.

## Development Rules

- Do not claim an instrument predicts the market. These modules produce analysis features, confluence scores, and structured signals for research and automation.
- Avoid look-ahead bias. If a detector needs future candles to confirm a swing, expose the confirmation timing.
- Keep indicators configurable. Forex pairs have different pip sizes, spreads, volatility profiles, and session behavior.
- Treat SMC concepts as rule-based heuristics, not universally standardized formulas.
- Add tests for every new instrument, especially around timestamp alignment and no-lookahead behavior.

## Fast Usage

```python
from smc_ta import analyze_forex

result = analyze_forex(candles, symbol="EURUSD")
features = result.features
signals = result.signals
gaps = result.fair_value_gaps
order_blocks = result.order_blocks
```

