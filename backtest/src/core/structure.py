"""Market-structure primitives: swing pivots, BOS/MSS, Fair Value Gaps,
Order Blocks. Pure pandas/numpy, timeframe-agnostic (works on whatever
bars you pass in — M1, M5, H1, H4...).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


def pivot_highs_lows(df: pd.DataFrame, left: int, right: int) -> pd.DataFrame:
    """Confirmed swing pivots, mirroring Pine's ta.pivothigh/ta.pivotlow:
    a pivot at bar i is confirmed `right` bars later (i.e. known only once
    bar i+right has closed), so we shift the boolean flags forward by
    `right` bars to avoid look-ahead.
    """
    n = len(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    is_ph = np.zeros(n, dtype=bool)
    is_pl = np.zeros(n, dtype=bool)
    for i in range(left, n - right):
        window_h = high[i - left : i + right + 1]
        window_l = low[i - left : i + right + 1]
        if high[i] == window_h.max() and np.sum(window_h == window_h.max()) == 1:
            is_ph[i] = True
        if low[i] == window_l.min() and np.sum(window_l == window_l.min()) == 1:
            is_pl[i] = True
    out = pd.DataFrame(index=df.index)
    out["pivot_high"] = np.where(is_ph, high, np.nan)
    out["pivot_low"] = np.where(is_pl, low, np.nan)
    # confirmed_at: index position where this pivot becomes knowable
    confirmed_idx = np.full(n, -1)
    for i in range(n):
        confirmed_idx[i] = min(i + right, n - 1)
    out["ph_confirmed_at"] = np.where(is_ph, df.index[confirmed_idx], pd.NaT)
    out["pl_confirmed_at"] = np.where(is_pl, df.index[confirmed_idx], pd.NaT)
    return out


@dataclass
class StructureEvent:
    ts: pd.Timestamp
    kind: str  # "BOS_UP" | "BOS_DOWN" | "MSS_UP" | "MSS_DOWN"
    level: float
    origin_ts: pd.Timestamp


def detect_structure_breaks(df: pd.DataFrame, left: int, right: int) -> list[StructureEvent]:
    """Walk bars in order, only using pivots once they are confirmed
    (no look-ahead), and flag the first close beyond the last confirmed
    opposite pivot as a structure break (BOS if it extends the prevailing
    trend, MSS if it reverses it).
    """
    piv = pivot_highs_lows(df, left, right)
    events: list[StructureEvent] = []

    last_ph = None  # (price, ts)
    last_pl = None
    broken_ph_ts = None
    broken_pl_ts = None
    trend = 0  # 1 up, -1 down, 0 unknown

    # Pre-index pivots by confirmation bar position for O(n) scan.
    ph_by_confirm: dict[pd.Timestamp, list[tuple[pd.Timestamp, float]]] = {}
    pl_by_confirm: dict[pd.Timestamp, list[tuple[pd.Timestamp, float]]] = {}
    for ts, row in piv.iterrows():
        if not np.isnan(row["pivot_high"]):
            ph_by_confirm.setdefault(row["ph_confirmed_at"], []).append((ts, row["pivot_high"]))
        if not np.isnan(row["pivot_low"]):
            pl_by_confirm.setdefault(row["pl_confirmed_at"], []).append((ts, row["pivot_low"]))

    for ts in df.index:
        close = df.at[ts, "close"]

        # Break test BEFORE registering any pivot confirmed on this bar
        # (mirrors the Pine engine's ordering comment).
        if last_ph is not None and last_ph[0] != broken_ph_ts and close > last_ph[1]:
            kind = "BOS_UP" if trend >= 0 else "MSS_UP"
            events.append(StructureEvent(ts, kind, last_ph[1], last_ph[0]))
            broken_ph_ts = last_ph[0]
            trend = 1
        if last_pl is not None and last_pl[0] != broken_pl_ts and close < last_pl[1]:
            kind = "BOS_DOWN" if trend <= 0 else "MSS_DOWN"
            events.append(StructureEvent(ts, kind, last_pl[1], last_pl[0]))
            broken_pl_ts = last_pl[0]
            trend = -1

        for pts, price in ph_by_confirm.get(ts, []):
            if last_ph is None or pts > last_ph[0]:
                last_ph = (pts, price)
        for pts, price in pl_by_confirm.get(ts, []):
            if last_pl is None or pts > last_pl[0]:
                last_pl = (pts, price)

    return events


def fair_value_gaps(df: pd.DataFrame) -> pd.DataFrame:
    """3-candle Fair Value Gaps. Bullish FVG at i: low[i] > high[i-2],
    zone = (high[i-2], low[i]). Bearish FVG at i: high[i] < low[i-2],
    zone = (high[i], low[i-2]).
    """
    high = df["high"]
    low = df["low"]
    bull = low > high.shift(2)
    bear = high < low.shift(2)
    out = pd.DataFrame(index=df.index)
    out["bull_fvg_low"] = np.where(bull, high.shift(2), np.nan)
    out["bull_fvg_high"] = np.where(bull, low, np.nan)
    out["bear_fvg_low"] = np.where(bear, high, np.nan)
    out["bear_fvg_high"] = np.where(bear, low.shift(2), np.nan)
    out["bull_fvg_ce"] = (out["bull_fvg_low"] + out["bull_fvg_high"]) / 2
    out["bear_fvg_ce"] = (out["bear_fvg_low"] + out["bear_fvg_high"]) / 2
    return out


def displacement_leg(df: pd.DataFrame, atr: pd.Series, i: int, direction: int,
                      body_atr_min: float = 0.5, range_atr_min: float = 1.2) -> bool:
    """Is bar i a displacement candle in `direction` (1 up, -1 down)?"""
    o, h, l, c = df.iloc[i][["open", "high", "low", "close"]]
    body = abs(c - o)
    rng = h - l
    a = atr.iloc[i]
    if a <= 0 or np.isnan(a):
        return False
    if direction > 0 and c <= o:
        return False
    if direction < 0 and c >= o:
        return False
    return (body / a) >= body_atr_min and (rng / a) >= range_atr_min


def order_block(df: pd.DataFrame, impulse_start_i: int, direction: int) -> tuple[int, float, float] | None:
    """Last opposite-colour candle immediately before the impulse leg that
    starts at `impulse_start_i`. direction=1 -> bullish impulse -> look for
    the last bearish candle before it (and vice versa).
    Returns (bar_index, ob_low, ob_high) or None.
    """
    for j in range(impulse_start_i - 1, max(-1, impulse_start_i - 15), -1):
        o, c = df.iloc[j][["open", "close"]]
        is_bear = c < o
        is_bull = c > o
        if direction > 0 and is_bear:
            return j, df.iloc[j]["low"], df.iloc[j]["high"]
        if direction < 0 and is_bull:
            return j, df.iloc[j]["low"], df.iloc[j]["high"]
    return None


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / length, adjust=False).mean()
