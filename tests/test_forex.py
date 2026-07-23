from __future__ import annotations

import pytest

from smc_ta.forex import infer_pip_size, position_size_units, spread_to_pips


def test_pip_size_conventions() -> None:
    assert infer_pip_size("EURUSD") == 0.0001
    assert infer_pip_size("USDJPY") == 0.01


def test_spread_to_pips() -> None:
    assert spread_to_pips(0.0002, "EURUSD") == pytest.approx(2.0)
    assert spread_to_pips(0.02, "USDJPY") == pytest.approx(2.0)


def test_position_size_units_quote_account() -> None:
    units = position_size_units(
        account_equity=10_000,
        risk_percent=1,
        entry=1.1000,
        stop=1.0950,
        symbol="EURUSD",
    )
    assert units == pytest.approx(20_000)

