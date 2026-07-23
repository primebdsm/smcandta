# Instruments

This document lists the implemented instruments and how each one is represented in code.

## Technical Analysis

- Moving averages: `sma`, `ema`, `wma`, `hma`
- Trend/momentum blend: `macd`
- Directional movement: `adx`
- Trend stop: `supertrend`
- Momentum: `rsi`, `stochastic`, `cci`, `roc`, `williams_r`
- Volatility: `true_range`, `atr`, `bollinger_bands`, `keltner_channels`, `donchian_channels`, `average_daily_range`
- Volume/tick-volume proxies: `obv`, `money_flow_index`, `vwap`
- Support/resistance: `pivot_points_standard`, `fibonacci_retracements`, `rolling_support_resistance`
- Candles: `doji`, `bullish_engulfing`, `bearish_engulfing`, `pin_bar`, `inside_bar`

## Smart Money Concept

- Swings: `swing_points`
- Structure: `market_structure`
- Fair Value Gaps: `fair_value_gaps`
- Active FVG features: `active_fvg_features`
- Order Blocks: `detect_order_blocks`
- Active OB features: `active_order_block_features`
- Liquidity pools: `equal_highs_lows`
- Liquidity sweeps: `liquidity_sweeps`
- Premium/discount: `premium_discount_zones`

## Symbiosis

Use `build_smc_ta_features` for a single candle-aligned feature table and `generate_confluence_signals` for long/short/flat analysis output.

