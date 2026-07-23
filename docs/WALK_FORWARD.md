# Walk-Forward Optimization

Walk-forward testing reduces overfitting by separating strategy selection from forward evaluation.

## How It Works

1. Split candles into a training window and the next unseen test window.
2. Run every candidate `BacktestConfig` on the training window.
3. Rank candidates by an objective such as return over drawdown.
4. Run only the selected candidate on the test window.
5. Slide forward and repeat.

The optimizer stores train metrics and test metrics separately. Test metrics are the only forward-performance estimate.

## Usage

```python
from smc_ta.backtest import BacktestConfig
from smc_ta.walkforward import WalkForwardCandidate, WalkForwardConfig, run_walk_forward

result = run_walk_forward(
    candles,
    candidates=[
        WalkForwardCandidate("base", BacktestConfig(symbol="EURUSD")),
        WalkForwardCandidate("strict", BacktestConfig(symbol="EURUSD")),
    ],
    config=WalkForwardConfig(train_size=500, test_size=150),
)

print(result.summary)
print(result.candidate_rankings)
```

## Outputs

- `folds`: full train/test fold objects
- `summary`: one row per fold with selected candidate and train/test metrics
- `candidate_rankings`: candidate scores per fold
- `combined_equity_curve`: concatenated out-of-sample equity
- `combined_trades`: concatenated out-of-sample trades

