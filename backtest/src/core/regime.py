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


def align_to(htf_series: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    """Forward-fill a higher-timeframe series onto a finer target index:
    each target timestamp gets the most recent HTF value known at or
    before it (no look-ahead)."""
    union_index = target_index.union(htf_series.index)
    return htf_series.reindex(union_index).ffill().reindex(target_index)
