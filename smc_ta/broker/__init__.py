"""Broker adapter interfaces and paper execution."""

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import (
    AccountState,
    OrderFill,
    OrderRequest,
    Position,
)
from smc_ta.broker.mt5 import MetaTrader5Broker, MetaTrader5CandleDataSource, Mt5UnavailableError
from smc_ta.broker.oanda import OandaBroker, OandaCandleDataSource, OandaConfig
from smc_ta.broker.paper import PaperBroker

__all__ = [
    "AccountState",
    "BrokerAdapter",
    "MetaTrader5Broker",
    "MetaTrader5CandleDataSource",
    "Mt5UnavailableError",
    "OandaBroker",
    "OandaCandleDataSource",
    "OandaConfig",
    "OrderFill",
    "OrderRequest",
    "PaperBroker",
    "Position",
]
