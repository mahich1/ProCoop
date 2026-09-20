from __future__ import annotations

import pandas as pd


def to_timeframe(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Resample OHLCV bars (tz-aware index) to a coarser timeframe.
    rule examples: '5min', '1h', '4h', '1D'.
    """
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df.columns:
        agg["volume"] = "sum"
    out = df.resample(rule, label="left", closed="left").agg(agg)
    return out.dropna(subset=["open", "high", "low", "close"])
