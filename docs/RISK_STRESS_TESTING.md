# Risk Stress Testing

Risk stress testing replays the demo-forward bot path under worse execution and market-condition assumptions.

It uses the same `run_demo_forward_test` engine that already exercises the bot, risk manager, paper broker, reconciliation, lifecycle, journal hooks, fills, and report generation.

This is not a prediction tool. It is a robustness check before broker-demo or live-money promotion.

## Stress Scenarios

The default scenarios include:

- `baseline`: unchanged demo-forward settings
- `wide_spread`: wider candle/broker spread
- `high_slippage`: larger order slippage
- `costly_execution`: wider spread, larger slippage, and extra commission
- `volatility_spike`: wider candle high/low ranges plus wider spread
- `half_risk`: lower risk percent and lower unit cap

Each scenario produces:

- demo-forward summary metrics
- net PnL delta versus baseline
- final equity delta versus baseline
- return delta versus baseline
- drawdown delta versus baseline
- warning/failure status when configured stress gates are breached

## CLI

Run on a real Forex candle CSV:

```bash
python examples/risk_stress_test.py EURUSD_M15.csv \
  --symbol EURUSD \
  --warmup-candles 150 \
  --max-cycles 500 \
  --max-drawdown-percent 15 \
  --output-dir reports/risk_stress/eurusd_m15
```

Run a deterministic local smoke test:

```bash
python examples/risk_stress_test.py \
  --max-cycles 25 \
  --output-dir reports/risk_stress/sample
```

## Python API

```python
from smc_ta import (
    DemoForwardConfig,
    RiskStressConfig,
    RiskStressScenario,
    run_risk_stress_test,
    write_risk_stress_report_bundle,
)

result = run_risk_stress_test(
    candles,
    config=RiskStressConfig(
        demo_forward=DemoForwardConfig(symbol="EURUSD", warmup_candles=150),
        scenarios=(
            RiskStressScenario("baseline"),
            RiskStressScenario("wide_spread", spread_multiplier=2.0, additional_spread_pips=0.5),
            RiskStressScenario("volatility_spike", range_multiplier=1.75, spread_multiplier=1.5),
        ),
        max_allowed_drawdown_percent=15,
        max_return_degradation_percent=5,
    ),
)

saved = write_risk_stress_report_bundle(result, "reports/risk_stress/eurusd_m15")
```

## Artifact Bundle

`write_risk_stress_report_bundle` writes:

- `summary.json`: full stress result and scenario metadata
- `scenarios.csv`: flat scenario comparison table
- `stress_report.html`: local HTML review report
- `scenarios/<scenario>/`: normal demo-forward report bundle for each successful scenario

## How It Works

1. Normalize the input candles.
2. For each scenario, adjust candle spread, high/low range, slippage, commission, risk percent, and max units according to the scenario.
3. Run the real demo-forward replay.
4. Compare scenario metrics against the first successful scenario as baseline.
5. Apply stress gates such as max drawdown, minimum final equity, minimum net PnL, and max return degradation.
6. Write report artifacts for review.

## Operational Use

Run stress tests after backtests, walk-forward optimization, demo-forward reports, and scheduled demo-forward runs.

Useful questions:

- Does the setup still survive wider spreads?
- Does slippage erase expected edge?
- Does extra commission make the strategy fragile?
- Does volatility trigger too many stop events?
- Does smaller risk sizing still produce enough evidence to justify promotion?
- Which scenario causes the worst drawdown or largest return degradation?

Risk stress testing can improve live-readiness indirectly by finding fragile assumptions before real broker execution. It does not guarantee profit.
