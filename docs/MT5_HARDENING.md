# MetaTrader 5 Hardening

The MT5 adapter is optional because it depends on the local `MetaTrader5` Python package and a running terminal session.

This hardening layer keeps the Python bot broker-neutral while adding MT5-specific readiness and execution checks.

## Main APIs

```python
from smc_ta import MetaTrader5Broker, Mt5Config

broker = MetaTrader5Broker(
    config=Mt5Config(
        login=123456,
        password="...",
        server="Broker-Demo",
        path="/path/to/terminal64.exe",
        symbol_aliases={"EURUSD": "EURUSD.m"},
        max_spread_points=25,
        max_tick_age_seconds=15,
    )
)

report = broker.practice_readiness(("EURUSD",))
if not report.ok:
    raise RuntimeError(report.summary())
```

## Readiness Checks

`practice_readiness()` is non-trading. It does not place, modify, or close orders.

It checks:

- terminal connection state
- terminal trade-allowed flag
- account probe
- real-account block unless `allow_real_account=True`
- symbol selection and visibility
- symbol trade mode
- point size, digits, min/max/step lot metadata
- broker stop-distance metadata
- fresh bid/ask tick
- maximum spread in MT5 points when configured

## Execution Hardening

Before `place_order()` or `close_position()`, the adapter now validates:

- terminal/account state
- real account safety flag
- symbol alias resolution
- symbol visibility/selection
- symbol trade mode
- current tick freshness
- spread limit
- units-to-lots conversion
- broker min lot, max lot, and lot step
- SL/TP direction
- SL/TP minimum stop distance
- optional MT5 `order_check()` before `order_send()`

MT5 trade failures raise MT5-specific exceptions:

- `Mt5InitializationError`
- `Mt5TerminalValidationError`
- `Mt5SymbolValidationError`
- `Mt5PriceValidationError`
- `Mt5OrderRejected`

## Symbol Aliases

Many MT5 brokers use suffixes or prefixes such as `EURUSD.m`, `EURUSD.a`, or `EURUSDpro`.

Keep strategy code normalized:

```python
Mt5Config(symbol_aliases={"EURUSD": "EURUSD.m"})
```

The bot can keep using `EURUSD`, while the adapter sends `EURUSD.m` to the terminal.

## Runtime Config

Environment variables:

```text
SMC_TA_BROKER=mt5
SMC_TA_MODE=demo
MT5_LOGIN=123456
MT5_PASSWORD=...
MT5_SERVER=Broker-Demo
MT5_PATH=/path/to/terminal64.exe
SMC_TA_MT5_MAX_SPREAD_POINTS=25
SMC_TA_MT5_MAX_TICK_AGE_SECONDS=15
SMC_TA_MT5_CHECK_ORDER_BEFORE_SEND=true
SMC_TA_MT5_ALLOW_REAL_ACCOUNT=false
```

Build adapter config:

```python
from smc_ta import RuntimeConfig, build_mt5_config

runtime = RuntimeConfig.from_env().assert_ready()
mt5_config = build_mt5_config(runtime)
broker = MetaTrader5Broker(config=mt5_config)
```

Live MT5 config requires the normal live arming controls and `mt5_allow_real_account=True`.

## CLI

Run a non-trading terminal check:

```bash
python examples/mt5_practice_check.py \
  --symbols EURUSD \
  --symbol-alias EURUSD=EURUSD.m \
  --max-spread-points 25 \
  --max-tick-age-seconds 15
```

Use `--allow-real-account` only when intentionally validating a real account. Demo account validation should leave it disabled.

## What Is Still Broker-Specific

MT5 brokers can differ in fill mode, minimum lot, contract size, stop levels, symbol names, execution policy, and terminal behavior.

Before live trading, still run:

- real terminal demo readiness checks
- minimum-size demo order validation
- broker restart sync and lifecycle recovery
- demo-forward reports using MT5 candles
- spread/slippage review from the selected MT5 broker
- manual intervention and terminal reconnect drills
