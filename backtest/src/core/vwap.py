from __future__ import annotations

import pandas as pd


def session_vwap(df: pd.DataFrame) -> pd.Series:
    """Volume-weighted average price, reset at the start of each NY
    calendar day (00:00 local). Requires a 'volume' column."""
    day = pd.Series(df.index.date, index=df.index)
    typical = (df["high"] + df["low"] + df["close"]) / 3
    pv = typical * df["volume"]
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["volume"].groupby(day).cumsum()
    return cum_pv / cum_vol
