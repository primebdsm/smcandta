"""Run walk-forward optimization on a Forex CSV file."""

from __future__ import annotations

import argparse

from smc_ta.backtest import BacktestConfig
from smc_ta.data import load_csv_candles
from smc_ta.engine import ConfluenceConfig
from smc_ta.risk import RiskConfig
from smc_ta.walkforward import WalkForwardCandidate, WalkForwardConfig, run_walk_forward


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--train-size", type=int, default=500)
    parser.add_argument("--test-size", type=int, default=150)
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path)
    candidates = [
        WalkForwardCandidate(
            "balanced",
            BacktestConfig(
                symbol=args.symbol,
                confluence=ConfluenceConfig(min_signal_score=6),
                risk=RiskConfig(min_confidence=0.45, min_reward_to_risk=1.2, max_units=10_000),
            ),
        ),
        WalkForwardCandidate(
            "selective",
            BacktestConfig(
                symbol=args.symbol,
                confluence=ConfluenceConfig(min_signal_score=7, adx_threshold=20),
                risk=RiskConfig(min_confidence=0.55, min_reward_to_risk=1.5, max_units=10_000),
            ),
        ),
    ]
    result = run_walk_forward(
        candles,
        candidates=candidates,
        config=WalkForwardConfig(train_size=args.train_size, test_size=args.test_size),
    )
    print(result.summary)


if __name__ == "__main__":
    main()

