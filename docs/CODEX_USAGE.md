# Codex Usage

Use this repository as a structured codebase, not as loose strategy notes.

## Main Entry Point

```python
from smc_ta import analyze_forex

result = analyze_forex(candles, symbol="GBPUSD")
```

The result object contains:

- `features`: candle-aligned SMC + TA feature table
- `signals`: candle-aligned confluence signal table
- `market_structure`: BOS/CHoCH and swing context
- `fair_value_gaps`: FVG event table
- `order_blocks`: OB event table
- `liquidity_pools`: equal-high/equal-low liquidity zones

## Bot Pattern

1. Load broker candles.
2. Normalize columns with `normalize_ohlcv`.
3. Run `analyze_forex`.
4. Read only the latest closed candle.
5. Apply your risk, news, spread, execution, and account rules.
6. Send orders through a separate broker adapter.

## Real News Provider

```python
from smc_ta.news import TradingEconomicsCalendarSource, TradingEconomicsConfig

calendar = TradingEconomicsCalendarSource(
    TradingEconomicsConfig.from_env(importance=(3,))
)
```

Use the provider to build the same `NewsFilter` object that the backtester and demo bot already accept. See `docs/NEWS_PROVIDERS.md`.

## Chart Snapshot

```python
from smc_ta import analyze_forex, write_analysis_chart

result = analyze_forex(candles, symbol="EURUSD")
write_analysis_chart("analysis_chart.html", result, symbol="EURUSD")
```

The chart is a review artifact for Codex, journals, alerts, or dashboard snapshots. It renders the existing analysis output and should not be used as a separate source of trading decisions.

## No-Lookahead Notes

Swing highs/lows require right-side candles for confirmation. The SMC structure module exposes both pivot candle fields and confirmation-time fields. Use confirmation-time columns when backtesting or trading live.

FVGs form after the third candle closes. Order blocks form after a displacement or structure event. These are event-based detectors and are safe to use after the event candle closes.
