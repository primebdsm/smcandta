"""Run demo-forward risk stress scenarios and write reports."""

from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from smc_ta import (
    DemoForwardConfig,
    RiskStressConfig,
    run_risk_stress_test,
    write_risk_stress_report_bundle,
)
from smc_ta.data import load_csv_candles
from smc_ta.risk import RiskConfig


def main() -> int:
    parser = argparse.ArgumentParser(description="Stress demo-forward results under worse execution conditions")
    parser.add_argument("csv_path", nargs="?", help="Optional OHLCV CSV. If omitted, a deterministic sample is used.")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output-dir", default="reports/risk_stress")
    parser.add_argument("--warmup-candles", type=int, default=120)
    parser.add_argument("--max-cycles", type=int, default=100)
    parser.add_argument("--initial-balance", type=float, default=10_000)
    parser.add_argument("--spread-pips", type=float, default=1.2)
    parser.add_argument("--slippage-pips", type=float, default=0.1)
    parser.add_argument("--commission-per-order", type=float, default=0.0)
    parser.add_argument("--risk-percent", type=float, default=0.5)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument("--min-rr", type=float, default=1.0)
    parser.add_argument("--max-units", type=float, default=10_000)
    parser.add_argument("--max-drawdown-percent", type=float, default=15.0)
    parser.add_argument("--min-final-equity", type=float)
    parser.add_argument("--min-net-pnl", type=float)
    parser.add_argument("--max-return-degradation-percent", type=float)
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path) if args.csv_path else _sample_candles()
    result = run_risk_stress_test(
        candles,
        config=RiskStressConfig(
            demo_forward=DemoForwardConfig(
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
            max_allowed_drawdown_percent=args.max_drawdown_percent,
            min_final_equity=args.min_final_equity,
            min_net_pnl=args.min_net_pnl,
            max_return_degradation_percent=args.max_return_degradation_percent,
        ),
    )
    saved = write_risk_stress_report_bundle(result, args.output_dir)

    print(saved.summary())
    if saved.artifacts is not None:
        print(f"summary_json={saved.artifacts.summary_json}")
        print(f"scenarios_csv={saved.artifacts.scenarios_csv}")
        print(f"html_report={saved.artifacts.html_report}")
    for item in saved.scenarios:
        print(f"{item.scenario.name}={item.status}:{item.message}")
    return 0 if saved.ok else 2


def _sample_candles(rows: int = 260) -> pd.DataFrame:
    index = pd.date_range("2024-01-01", periods=rows, freq="15min", tz="UTC")
    wave = np.sin(np.arange(rows) / 5) * 0.001
    drift = np.arange(rows) * 0.00002
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
            "tick_volume": 100 + (np.arange(rows) % 25),
            "spread": 0.00012,
        },
        index=index,
    )


if __name__ == "__main__":
    raise SystemExit(main())
