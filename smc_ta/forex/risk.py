"""Risk helper functions for Forex bots."""

from __future__ import annotations

from smc_ta.forex.pairs import forex_pair_spec, infer_pip_size


def reward_to_risk(entry: float, stop: float, target: float) -> float:
    """Return reward/risk ratio from entry, stop, and target prices."""

    risk = abs(entry - stop)
    if risk == 0:
        raise ValueError("entry and stop cannot be equal")
    reward = abs(target - entry)
    return reward / risk


def pip_value_per_unit(
    symbol: str,
    price: float,
    *,
    account_currency: str = "USD",
    quote_to_account_rate: float | None = None,
) -> float:
    """Return account-currency pip value for one base unit.

    If the account currency equals the quote currency, one pip per unit equals
    pip size. If account currency differs, pass `quote_to_account_rate`.
    """

    spec = forex_pair_spec(symbol)
    account = account_currency.upper()
    if account == spec.quote_currency:
        return spec.pip_size
    if account == spec.base_currency:
        return spec.pip_size / price
    if quote_to_account_rate is None:
        raise ValueError("quote_to_account_rate is required for cross-currency pip value")
    return spec.pip_size * quote_to_account_rate


def position_size_units(
    account_equity: float,
    risk_percent: float,
    entry: float,
    stop: float,
    symbol: str,
    *,
    account_currency: str = "USD",
    quote_to_account_rate: float | None = None,
) -> float:
    """Return position size in base-currency units for fixed-percent risk."""

    if account_equity <= 0:
        raise ValueError("account_equity must be positive")
    if risk_percent <= 0:
        raise ValueError("risk_percent must be positive")
    if entry == stop:
        raise ValueError("entry and stop cannot be equal")

    pip_size = infer_pip_size(symbol)
    risk_amount = account_equity * (risk_percent / 100.0)
    risk_pips = abs(entry - stop) / pip_size
    value_per_unit = pip_value_per_unit(
        symbol,
        entry,
        account_currency=account_currency,
        quote_to_account_rate=quote_to_account_rate,
    )
    return risk_amount / (risk_pips * value_per_unit)

