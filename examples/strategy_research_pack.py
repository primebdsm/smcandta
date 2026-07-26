"""Run a repeatable SMC + TA strategy research pack from CSV candles."""

from __future__ import annotations

import argparse

from smc_ta.data import load_csv_candles
from smc_ta.research import (
    StrategyResearchConfig,
    StrategyResearchGrid,
    StrategyResearchHypothesis,
    run_strategy_research_pack,
    write_strategy_research_pack,
)
from smc_ta.walkforward import WalkForwardConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SMC TA strategy research on historical candles")
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--profile", default="intraday_m15")
    parser.add_argument("--hypothesis", default="custom_research")
    parser.add_argument("--thesis", default="Custom SMC/TA research hypothesis.")
    parser.add_argument("--output-dir", default="reports/strategy_research")
    parser.add_argument("--max-candidates", type=int, default=18)
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--train-size", type=int, default=500)
    parser.add_argument("--test-size", type=int, default=150)
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path)
    hypothesis = StrategyResearchHypothesis(
        name=args.hypothesis,
        profile_name=args.profile,
        thesis=args.thesis,
        symbols=(args.symbol.upper(),),
    )
    config = StrategyResearchConfig(
        hypotheses=(hypothesis,),
        grid=StrategyResearchGrid(max_candidates_per_hypothesis=args.max_candidates),
        symbols=(args.symbol.upper(),),
        run_walk_forward=args.walk_forward,
        walk_forward=WalkForwardConfig(train_size=args.train_size, test_size=args.test_size)
        if args.walk_forward
        else None,
    )
    result = run_strategy_research_pack({args.symbol.upper(): candles}, config=config)
    saved = write_strategy_research_pack(result, args.output_dir)
    print(result.summary())
    if saved.artifacts is not None:
        print(saved.artifacts.html_report)


if __name__ == "__main__":
    main()
