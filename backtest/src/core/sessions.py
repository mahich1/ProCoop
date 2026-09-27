"""Session/killzone windows, all expressed in America/New_York local time.

Bars passed to these helpers must have a tz-aware DatetimeIndex in
America/New_York (see data.databento_fetch.to_ny_time).
"""
from __future__ import annotations

import pandas as pd

# Standard ICT session/killzone windows (NY local time), matching the
# defaults used in the Pine indicator supplied by the user.
ASIA_SESSION = ("00:00", "08:00")
LONDON_SESSION = ("02:00", "05:00")
LONDON_KILLZONE = ("02:00", "05:00")
NY_AM_KILLZONE = ("08:30", "11:00")
OR_WINDOW = ("09:30", "09:45")
NY_TRADE_SESSION = ("09:35", "16:00")

# "ICT 2022 model" session windows: index futures AM session and the PM session.
ICT2022_AM_SESSION = ("08:30", "11:00")
ICT2022_PM_SESSION = ("13:30", "16:00")


def in_window(index: pd.DatetimeIndex, start: str, end: str) -> pd.Series:
    """Boolean mask: bar's NY local time-of-day falls in [start, end)."""
    t = index.strftime("%H:%M")
    if start <= end:
        return pd.Series((t >= start) & (t < end), index=index)
    # window crosses midnight
    return pd.Series((t >= start) | (t < end), index=index)


def session_date(index: pd.DatetimeIndex) -> pd.Series:
    """NY trading-day label. A session runs 00:00-23:59 NY local, so this is
    just the local calendar date."""
    return pd.Series(index.date, index=index)


def session_high_low(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """Per trading-day high/low realised during [start, end) NY time.

    Returns a DataFrame indexed by session date with columns
    high, low, start_ts, end_ts (timestamps of the extreme bars).
    """
    mask = in_window(df.index, start, end)
    sub = df.loc[mask.values]
    if sub.empty:
        return pd.DataFrame(columns=["high", "low", "high_ts", "low_ts"])
    day = pd.Series(sub.index.date, index=sub.index)
    out = []
    for d, g in sub.groupby(day):
        hi_ts = g["high"].idxmax()
        lo_ts = g["low"].idxmin()
        out.append({
            "date": d,
            "high": g.loc[hi_ts, "high"],
            "low": g.loc[lo_ts, "low"],
            "high_ts": hi_ts,
            "low_ts": lo_ts,
        })
    res = pd.DataFrame(out).set_index("date")
    res.index = pd.to_datetime(res.index)  # tz-naive Timestamp, not raw date objects -
    # callers look this up with pd.Timestamp(ts.date()), which never matches a plain
    # datetime.date (different type, same day) even though `in` returns no error.
    return res


def previous_day_high_low(df: pd.DataFrame) -> pd.DataFrame:
    """Full-session (00:00-23:59 NY) daily high/low, shifted by one day so
    each row holds the PDH/PDL applicable to that trading day."""
    day = pd.Series(df.index.date, index=df.index)
    daily = df.groupby(day).agg(high=("high", "max"), low=("low", "min"))
    daily.index = pd.to_datetime(daily.index)
    pdh_pdl = daily.shift(1)
    pdh_pdl.columns = ["pdh", "pdl"]
    return pdh_pdl
