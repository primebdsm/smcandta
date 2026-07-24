# Demo-Forward Reports

The demo-forward package replays closed candles through the same `DemoTradingBot` path used for paper/demo integration and writes evidence artifacts for review.

This is not a backtest replacement. A backtest simulates strategy behavior inside the backtest engine. Demo-forward replay exercises the bot integration path: broker adapter, risk manager, reconciliation ledger, lifecycle store, journal hooks, paper fills, equity snapshots, and report generation.

## Main APIs

```python
from smc_ta import DemoForwardConfig, run_demo_forward_test, write_demo_forward_report_bundle
from smc_ta.risk import RiskConfig

result = run_demo_forward_test(
    candles,
    config=DemoForwardConfig(
        symbol="EURUSD",
        warmup_candles=120,
        risk=RiskConfig(
            risk_percent_per_trade=0.5,
            min_confidence=0.5,
            min_reward_to_risk=1.0,
            max_units=10_000,
        ),
    ),
)

result = write_demo_forward_report_bundle(result, "reports/demo_forward")
print(result.summary)
```

## CLI

Run on the deterministic sample:

```bash
python examples/demo_forward_report.py --output-dir reports/demo_forward
```

Run on a real Forex candle CSV:

```bash
python examples/demo_forward_report.py EURUSD_M15.csv \
  --symbol EURUSD \
  --warmup-candles 150 \
  --max-cycles 500 \
  --output-dir reports/demo_forward_eurusd_m15
```

## Report Artifacts

The report bundle writes:

- `summary.json`: headline metrics and health status
- `report.html`: local HTML review report
- `cycles.csv`: one bot cycle per closed candle
- `equity_curve.csv`: balance, equity, and open-position count
- `trades.csv`: paper positions with setup names and realized PnL
- `fills.csv`: paper broker fills with spread/slippage/commission
- `setup_report.csv`: cycles, trades, net PnL, average PnL, and win rate by setup
- `session_report.csv`: cycles, orders, blocks, trades, and net PnL by Forex session
- `daily_report.csv`: daily equity, orders, blocks, trades, and net PnL
- `blocked_reasons.csv`: block reason counts and first/last seen timestamps
- `position_events.csv`: paper stop-loss, take-profit, and final-close events

## How It Works

1. Normalize OHLCV candles.
2. Keep a warmup window for indicators and SMC confirmation.
3. Feed each new closed candle into `DemoTradingBot.run_cycle`.
4. Record cycle action, signal side, setup name, block reasons, fills, and account state.
5. With the default `PaperBroker`, simulate broker-side SL/TP closes from candle high/low data.
6. Update the reconciliation ledger and lifecycle store when paper positions close.
7. Close remaining paper positions at the final candle when configured.
8. Write CSV, JSON, and HTML report artifacts.

## What It Measures

The package helps answer:

- Does the bot integration path create orders only when risk allows?
- Which setup names are producing orders, losses, wins, or blocks?
- Which Forex sessions are producing the most activity and PnL?
- Are most cycles blocked, and why?
- Are spread, slippage, commission, and paper broker fills visible?
- Does the equity curve stay within acceptable drawdown during replay?

## Profit Impact

This does not guarantee profit or predict the Forex market.

It can improve profit potential indirectly by creating a feedback loop. You can compare setup performance, sessions, risk blocks, spread/slippage, and daily behavior before moving from paper replay to broker demo testing. That reduces the chance of trading live with an unmeasured or operationally fragile strategy.

## Recommended Use Before Live

1. Run backtests with spread/slippage.
2. Run walk-forward optimization.
3. Run demo-forward reports on recent out-of-sample candles.
4. Run OANDA practice execution validation.
5. Run broker restart sync on every demo process start.
6. Collect at least 2-4 weeks of broker-demo reports before live-money trading.
