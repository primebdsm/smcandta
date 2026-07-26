# Strategy Research Pack

The strategy research pack turns SMC + TA strategy ideas into repeatable evidence.

It is research tooling only. It does not predict the Forex market and it does not authorize live trading.

## What It Adds

- named research hypotheses
- deterministic candidate grids around existing strategy profiles
- backtest metrics for each candidate
- setup-level evidence using the SMC setup classifier
- session-level evidence using Forex session labels
- optional walk-forward evidence
- promotion grading for `demo_candidate`, `research_only`, and `blocked`
- JSON, CSV, and standalone HTML reports

## Python Usage

```python
from smc_ta import StrategyResearchConfig, run_strategy_research_pack, write_strategy_research_pack

result = run_strategy_research_pack(
    {"EURUSD": candles},
    config=StrategyResearchConfig(symbols=("EURUSD",)),
)

write_strategy_research_pack(result, "reports/strategy_research")
```

## CLI Usage

```bash
python examples/strategy_research_pack.py EURUSD_M15.csv \
  --symbol EURUSD \
  --profile intraday_m15 \
  --hypothesis intraday_fvg_eurusd \
  --walk-forward \
  --output-dir reports/strategy_research/EURUSD_M15
```

## How It Works

1. Start from a `StrategyResearchHypothesis`.
2. Load the named strategy profile, such as `intraday_m15` or `london_killzone`.
3. Expand a controlled grid around profile settings:
   - minimum confluence score
   - ADX threshold
   - point-of-interest ATR distance
   - maximum spread filter
   - risk percent per trade
4. Run each candidate through the existing backtester.
5. Classify signals by SMC setup name.
6. Summarize signals and trades by setup and Forex session.
7. Optionally run walk-forward selection on the best candidates.
8. Grade each candidate against research gates.

## Output Files

- `summary.json`
- `hypotheses.csv`
- `candidates.csv`
- `setup_report.csv`
- `session_report.csv`
- `walk_forward_summary.csv`
- `walk_forward_rankings.csv`
- `promotion_report.csv`
- `research_report.html`

## Promotion Status

`demo_candidate` means the candidate passed the configured research gates on the supplied candles.

`research_only` means the candidate produced evidence but did not pass one or more gates, such as minimum trades, return, profit factor, win rate, or walk-forward evidence.

`blocked` means the candidate violated a hard safety gate, such as excessive drawdown or missing data.

Before real trading, a `demo_candidate` still needs out-of-sample data, demo-forward testing, stress testing, broker execution validation, monitoring, and operator review.
