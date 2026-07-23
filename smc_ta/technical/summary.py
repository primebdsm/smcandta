"""Convenience feature builder for technical analysis."""

from __future__ import annotations

import pandas as pd

from smc_ta.technical.candles import (
    bearish_engulfing,
    bullish_engulfing,
    doji,
    inside_bar,
    pin_bar,
)
from smc_ta.technical.momentum import cci, roc, rsi, stochastic, williams_r
from smc_ta.technical.support_resistance import rolling_support_resistance
from smc_ta.technical.trend import adx, ema, hma, macd, sma, supertrend, wma
from smc_ta.technical.volatility import atr, bollinger_bands, donchian_channels, keltner_channels
from smc_ta.technical.volume import money_flow_index, obv, vwap
from smc_ta.validation import normalize_ohlcv


def build_technical_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Build a broad, candle-aligned technical-analysis feature table."""

    data = normalize_ohlcv(df, copy=False)
    out = pd.DataFrame(index=data.index)

    out["sma_20"] = sma(data["close"], 20)
    out["sma_50"] = sma(data["close"], 50)
    out["ema_20"] = ema(data["close"], 20)
    out["ema_50"] = ema(data["close"], 50)
    out["wma_20"] = wma(data["close"], 20)
    out["hma_20"] = hma(data["close"], 20)
    out = out.join(macd(data["close"]))
    out = out.join(adx(data, 14).add_prefix("di_"))
    out = out.join(supertrend(data).add_prefix("st_"))

    out["rsi_14"] = rsi(data["close"], 14)
    out = out.join(stochastic(data))
    out["cci_20"] = cci(data, 20)
    out["roc_12"] = roc(data["close"], 12)
    out["williams_r_14"] = williams_r(data, 14)

    out["atr_14"] = atr(data, 14)
    out = out.join(bollinger_bands(data["close"]))
    out = out.join(keltner_channels(data))
    out = out.join(donchian_channels(data))

    out["obv"] = obv(data)
    out["mfi_14"] = money_flow_index(data)
    out["vwap"] = vwap(data)

    out = out.join(rolling_support_resistance(data))
    out["candle_doji"] = doji(data)
    out["candle_bullish_engulfing"] = bullish_engulfing(data)
    out["candle_bearish_engulfing"] = bearish_engulfing(data)
    out["candle_pin_bar"] = pin_bar(data)
    out["candle_inside_bar"] = inside_bar(data)

    return out

