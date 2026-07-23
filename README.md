# SMC TA Forex Toolkit

A Codex-ready Python repository for combining Smart Money Concept analysis and classical technical analysis in Forex bots.

This project is not a broker, execution system, or profit guarantee. It produces structured analysis features that can be tested, backtested, and integrated into a Python trading or analysis bot.

## What Is Included

Smart Money Concept instruments:

- Swing structure with confirmed swing timing
- Break of Structure and Change of Character
- Fair Value Gaps with mitigation/fill tracking
- Order Blocks with mitigation/invalidation tracking
- Buy-side and sell-side liquidity sweeps
- Equal highs/lows liquidity pools
- Premium, discount, and equilibrium zones
- Forex session labels and kill-zone helpers

Technical analysis instruments:

- SMA, EMA, WMA, HMA
- MACD
- ADX and directional indicators
- Supertrend
- RSI
- Stochastic oscillator
- CCI
- ROC
- Williams %R
- True Range and ATR
- Bollinger Bands
- Keltner Channels
- Donchian Channels
- Average Daily Range
- OBV, Money Flow Index, and VWAP/tick-volume helpers
- Pivot points, Fibonacci levels, rolling support/resistance
- Doji, engulfing, pin bar, and inside-bar candle patterns

Symbiosis engine:

- Builds one aligned feature table from SMC and TA modules
- Scores long/short confluence
- Requires both SMC context and TA confirmation before producing directional signals
- Includes spread, volatility, session, and point-of-interest filters

Live-readiness components:

- Broker-neutral execution interface
- Paper broker for demo forward testing
- CSV historical data source
- Backtester with spread, slippage, and commission
- Economic news blocking filter
- Risk manager for position sizing and exposure limits
- CSV journal and monitoring metrics

## Install

```bash
python3 -m pip install -e ".[dev]"
```

## Minimal Bot Integration

```python
import pandas as pd
from smc_ta import analyze_forex

candles = pd.read_csv("EURUSD_M15.csv", parse_dates=["time"], index_col="time")
result = analyze_forex(candles, symbol="EURUSD")

latest = result.signals.iloc[-1]
print(latest[["side", "confidence", "long_score", "short_score", "reasons"]])
```

## Backtest With Costs

```python
from smc_ta.backtest import BacktestConfig, run_backtest

result = run_backtest(
    candles,
    config=BacktestConfig(symbol="EURUSD", spread_pips=1.2, slippage_pips=0.1),
)
```

## Demo Forward Test

```python
from smc_ta.broker import PaperBroker
from smc_ta.live import DemoTradingBot
from smc_ta.risk import RiskManager

bot = DemoTradingBot(
    symbol="EURUSD",
    broker=PaperBroker(initial_balance=10_000),
    risk_manager=RiskManager(),
)
cycle = bot.run_cycle(candles)
```

## Input Format

The package expects a pandas DataFrame with these columns:

```text
open, high, low, close
```

Optional columns:

```text
volume, tick_volume, spread
```

Broker/export column names can be normalized:

```python
from smc_ta.validation import normalize_ohlcv

df = normalize_ohlcv(raw_df)
```

## Forex Reality

Forex is decentralized, broker spreads vary, and spot FX volume is normally not centralized. The volume tools in this repository treat broker tick volume as a proxy, not as exchange-wide volume.

Retail Forex is high risk, especially with leverage. See:

- [NFA Forex Transactions Regulatory Guide](https://www.nfa.futures.org/members/member-resources/files/forex-regulatory-guide.html)
- [CFTC Foreign Exchange Currency Fraud advisory](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/cftcnasaaforexalert.html)
- [TA-Lib functions list](https://ta-lib.org/functions/)

## Project Map

```text
smc_ta/
  technical/      Classical technical-analysis indicators
  smc/            Smart Money Concept detectors
  forex/          Pair metadata, pip/risk helpers, sessions
  engine/         SMC + TA confluence engine
  broker/         Broker interface and paper execution
  data/           Historical candle sources
  backtest/       Spread/slippage-aware backtester
  news/           Economic calendar filters
  risk/           Position sizing and exposure controls
  live/           Demo-forward bot orchestration
  journal/        CSV trade journal
  monitoring/     Equity and strategy health metrics
docs/             Codex, bot, and instrument documentation
examples/         Working Python examples
tests/            Deterministic pytest coverage
```

## Status

This is a real, usable analysis library for Forex research, backtesting, paper execution, and broker-adapter integration. It does not include a live broker connector with credentials. Before live use, implement and demo-test the broker-specific adapter for your venue.
