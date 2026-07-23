# Portfolio / Correlation Risk

Portfolio risk checks whether an approved trade is still acceptable when combined with open positions.

## What It Checks

- Total open position count
- Symbol concentration
- Opposite exposure on the same symbol
- Gross and net currency exposure
- Same-currency directional concentration
- Optional return-correlation concentration

## Forex Exposure Logic

For a long `EURUSD` position:

- EUR exposure is positive
- USD exposure is negative

For a short `EURUSD` position:

- EUR exposure is negative
- USD exposure is positive

This lets the bot detect hidden concentration. For example, `EURUSD` long and `GBPUSD` long are both short USD exposure.

## Usage

```python
from smc_ta.risk import PortfolioRiskConfig, PortfolioRiskManager

portfolio_risk = PortfolioRiskManager(
    PortfolioRiskConfig(
        max_total_open_positions=3,
        max_same_currency_direction_positions=2,
        max_correlated_positions=1,
    )
)

decision = portfolio_risk.evaluate_order(
    order,
    open_positions=broker.get_open_positions(),
    market_price=1.1000,
)
```

## Correlations

```python
from smc_ta.risk import compute_return_correlations

matrix = compute_return_correlations({
    "EURUSD": eurusd_candles,
    "GBPUSD": gbpusd_candles,
    "USDCHF": usdchf_candles,
})
```

Pass the matrix into `PortfolioRiskManager` to limit new trades that are highly correlated with existing positions.

