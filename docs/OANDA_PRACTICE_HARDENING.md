# OANDA Practice Hardening

The OANDA adapter now includes practice-mode safeguards for broker-specific execution work.

This hardening is designed to catch common demo/live integration failures before an order is submitted. It does not guarantee profit and it does not replace demo forward testing.

## Main APIs

```python
from smc_ta.broker import OandaBroker, OandaConfig

config = OandaConfig(
    account_id="...",
    token="...",
    practice=True,
    max_spread_pips=2.0,
    max_price_age_seconds=15.0,
    max_order_slippage_pips=1.0,
)

broker = OandaBroker(config)
report = broker.practice_readiness(("EURUSD", "GBPUSD"))
print(report.summary())
```

## Non-Trading Practice Check

Set:

```bash
export OANDA_ACCOUNT_ID="..."
export OANDA_TOKEN="..."
```

Run:

```bash
python examples/oanda_practice_check.py --symbols EURUSD,GBPUSD --max-spread-pips 2
```

Exit codes:

- `0`: account, instrument metadata, and pricing checks passed
- `2`: at least one blocking check failed

The check does not place orders.

## What Was Hardened

### API Client

- Conservative retry support for safe methods
- Default retries only for `GET`
- Retry handling for temporary statuses such as rate limits and server errors
- API error classification:
  - `OandaApiError`
  - `OandaRateLimitError`
  - `OandaConnectionError`
  - `OandaOrderRejected`

Market-order `POST` requests are not retried by default because retrying execution requests can duplicate risk if the first request succeeded but the response was lost.

### Instrument Metadata

`OandaBroker.get_instrument_spec(symbol)` loads account-specific metadata from OANDA and caches it.

It validates:

- instrument availability for the account
- `displayPrecision`
- `tradeUnitsPrecision`
- `pipLocation`
- `minimumTradeSize`
- `maximumOrderUnits`
- `marginRate`

Before order placement, units are checked against trade precision and min/max order size.

### Pricing Gate

`OandaBroker.get_price(symbol)` loads current bid/ask pricing and checks:

- price has bid and ask
- price is tradeable when `enforce_tradeable_price=True`
- price is not older than `max_price_age_seconds`
- spread is not wider than `max_spread_pips`

For market orders, the adapter can use the current OANDA bid/ask as the execution reference instead of blindly trusting the latest candle close.

### Market Order Payload

Before sending an order, the adapter now:

- formats OANDA instrument names such as `EUR_USD`
- formats prices using OANDA display precision
- formats signed units using OANDA trade-unit precision
- adds `timeInForce`
- keeps `positionFill` configurable
- keeps `clientExtensions` configurable
- optionally adds `priceBound` using `max_order_slippage_pips`
- raises `OandaOrderRejected` if OANDA returns no fill transaction

## Example Order Safety Config

```python
config = OandaConfig(
    account_id="...",
    token="...",
    practice=True,
    max_spread_pips=2.0,
    max_price_age_seconds=10.0,
    max_order_slippage_pips=1.0,
    market_order_time_in_force="FOK",
    position_fill="DEFAULT",
)
```

With this config, a buy order is blocked locally if:

- EURUSD is unavailable for the account
- units are fractional when OANDA requires integer units
- units are below `minimumTradeSize`
- units are above `maximumOrderUnits`
- OANDA price is not tradeable
- OANDA price is stale
- spread is wider than the configured limit

## What Still Needs Real Demo Testing

Code-level hardening is not the same as broker certification.

Before live trading, still run:

- practice-account readiness check
- candle download check for each symbol/timeframe
- one-unit or minimum-unit demo order test
- stop-loss/take-profit demo order test
- close-position demo test
- rejected-order test using intentionally invalid size
- restart and reconciliation test
- spread/slippage comparison against backtest assumptions
- emergency-stop close-all test in practice mode

## Official API References

- OANDA v20 development guide and practice/live URLs: https://developer.oanda.com/rest-live-v20/development-guide/
- OANDA account instruments endpoint: https://developer.oanda.com/rest-live-v20/account-ep/
- OANDA pricing endpoint: https://developer.oanda.com/rest-live-v20/pricing-ep/
- OANDA order endpoint: https://developer.oanda.com/rest-live-v20/order-ep/
