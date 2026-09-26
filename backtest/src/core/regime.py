"""Higher-timeframe regime/bias helpers built purely from OHLCV - no order
flow or external data required. Used to gate lower-timeframe signals by
the prevailing higher-timeframe trend.
"""
from __future__ import annotations

import pandas as pd


def ema_bias(htf: pd.DataFrame, ema_len: int) -> pd.Series:
    """+1 where the HTF close is above its EMA (bullish), -1 where below."""
    ema = htf["close"].ewm(span=ema_len, adjust=False).mean()
    return (htf["close"] > ema).astype(int) * 2 - 1


def align_to(htf_series: pd.Series, target_index: pd.DatetimeIndex, lag: int = 1) -> pd.Series:
    """Forward-fill a higher-timeframe series onto a finer target index.

    `to_timeframe` labels an HTF bar by its OPEN time (label="left"), so
    the bar labeled e.g. 10:00 for a 1h rule only finishes closing at
    11:00 - its 'close' (and anything derived from it, like an EMA) isn't
    really known until then. `lag=1` (default) shifts the series by one
    HTF bar before aligning so a target timestamp only ever sees a fully
    closed HTF bar's value, never the one still forming under it.
    """
    if lag:
        htf_series = htf_series.shift(lag)
    union_index = target_index.union(htf_series.index)
    return htf_series.reindex(union_index).ffill().reindex(target_index)
