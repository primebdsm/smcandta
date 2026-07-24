"""Broker adapter interfaces and paper execution."""

from smc_ta.broker.base import BrokerAdapter
from smc_ta.broker.models import (
    AccountState,
    BrokerOrder,
    OrderFill,
    OrderRequest,
    Position,
)
from smc_ta.broker.mt5 import MetaTrader5Broker, MetaTrader5CandleDataSource, Mt5UnavailableError
from smc_ta.broker.oanda import (
    OandaApiError,
    OandaBroker,
    OandaCandleDataSource,
    OandaClient,
    OandaConfig,
    OandaConnectionError,
    OandaInstrumentSpec,
    OandaInstrumentValidationError,
    OandaOrderRejected,
    OandaPracticeReadinessReport,
    OandaPriceSnapshot,
    OandaPriceValidationError,
    OandaRateLimitError,
)
from smc_ta.broker.oanda_validation import (
    OandaExecutionSample,
    OandaExecutionValidationCheck,
    OandaExecutionValidationConfig,
    OandaExecutionValidationReport,
    OandaPracticeExecutionValidator,
    run_oanda_practice_execution_validation,
)
from smc_ta.broker.paper import PaperBroker

__all__ = [
    "AccountState",
    "BrokerOrder",
    "BrokerAdapter",
    "MetaTrader5Broker",
    "MetaTrader5CandleDataSource",
    "Mt5UnavailableError",
    "OandaApiError",
    "OandaBroker",
    "OandaCandleDataSource",
    "OandaClient",
    "OandaConfig",
    "OandaConnectionError",
    "OandaExecutionSample",
    "OandaExecutionValidationCheck",
    "OandaExecutionValidationConfig",
    "OandaExecutionValidationReport",
    "OandaInstrumentSpec",
    "OandaInstrumentValidationError",
    "OandaOrderRejected",
    "OandaPracticeReadinessReport",
    "OandaPracticeExecutionValidator",
    "OandaPriceSnapshot",
    "OandaPriceValidationError",
    "OandaRateLimitError",
    "OrderFill",
    "OrderRequest",
    "PaperBroker",
    "Position",
    "run_oanda_practice_execution_validation",
]
