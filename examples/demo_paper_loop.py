"""One-cycle demo forward test using PaperBroker.

In production this script's `candles` input should come from your data vendor
after a candle closes.
"""

from __future__ import annotations

import argparse

from smc_ta.broker import PaperBroker
from smc_ta.data import load_csv_candles
from smc_ta.live import DemoTradingBot
from smc_ta.risk import RiskConfig, RiskManager


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--symbol", default="EURUSD")
    args = parser.parse_args()

    candles = load_csv_candles(args.csv_path)
    broker = PaperBroker(initial_balance=10_000)
    bot = DemoTradingBot(
        symbol=args.symbol,
        broker=broker,
        risk_manager=RiskManager(RiskConfig(risk_percent_per_trade=0.5)),
    )
    result = bot.run_cycle(candles)
    print(result)
    print(broker.get_account())


if __name__ == "__main__":
    main()

