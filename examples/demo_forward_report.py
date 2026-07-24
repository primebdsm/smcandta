"""Run a demo-forward replay and write report artifacts."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from smc_ta.data import load_csv_candles
from smc_ta.forwardtest import DemoForwardConfig, run_demo_forward_test, write_demo_forward_report_bundle
from smc_ta.risk import RiskConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate demo-forward testing reports")
    parser.add_argument("csv_path", nargs="?", help="Optional OHLCV CSV. If omitted, a deterministic sample is used.")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output-dir", default="reports/demo_forward")
    parser.add_argument("--warmup-candles", type=int, default=120)
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--initial-balance", type=float, default=10_000)
    parser.add_argument("--spread-pips", type=float, default=1.2)
    parser.add_argument("--slippage-pips", type=float, default=0.1)
    parser.add_argument("--commission-per-order", type=float, default=0.0)
    parser.add_argument("--risk-percent", type=float, default=0.5)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--min-rr", type=float, default=1.0)
    parser.add_argument("--max-units", type=float, default=10_000)
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path) if args.csv_path else _sample_candles()
    result = run_demo_forward_test(
        candles,
        config=DemoForwardConfig(
            symbol=args.symbol,
            initial_balance=args.initial_balance,
            warmup_candles=args.warmup_candles,
            max_cycles=args.max_cycles,
            default_spread_pips=args.spread_pips,
            slippage_pips=args.slippage_pips,
            commission_per_order=args.commission_per_order,
            risk=RiskConfig(
                risk_percent_per_trade=args.risk_percent,
                min_confidence=args.min_confidence,
                min_reward_to_risk=args.min_rr,
                max_units=args.max_units,
            ),
        ),
    )
    saved = write_demo_forward_report_bundle(result, args.output_dir)
    print(f"summary: {result.summary}")
    print(f"report: {saved.artifacts.html_report}")
    print(f"summary_json: {saved.artifacts.summary_json}")
    return 0 if result.ok else 2


def _sample_candles(n: int = 220) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    wave = np.sin(np.arange(n) / 5) * 0.001
    drift = np.arange(n) * 0.00002
    close = pd.Series(1.1000 + wave + drift, index=index)
    open_ = close.shift(1).fillna(close.iloc[0])
    high = pd.concat([open_, close], axis=1).max(axis=1) + 0.0004
    low = pd.concat([open_, close], axis=1).min(axis=1) - 0.0004
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "tick_volume": 100 + (np.arange(n) % 25),
            "spread": 0.00012,
        },
        index=index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
