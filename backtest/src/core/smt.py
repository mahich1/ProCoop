"""SMT (Smart Money Technique) divergence between two correlated instruments,
mirroring the Pine indicator's approach: match swing pivots of the same type
across both symbols within a small time offset, then flag divergence when
one instrument makes a new extreme the other fails to confirm.
"""
from __future__ import annotations

import pandas as pd

from .structure import pivot_highs_lows


def smt_at(primary: pd.DataFrame, secondary: pd.DataFrame, ts: pd.Timestamp,
           direction: int, left: int = 3, right: int = 1,
           sync_bars: int = 1, lookback_pivots: int = 5) -> bool:
    """Was there an SMT divergence confirmed at/around `ts`?

    direction=1 (bullish raid, i.e. sweep of a low): SMT+ = primary makes a
    LOWER low while secondary fails to make a new low (higher low) ->
    bullish divergence supporting a long.
    direction=-1 (sweep of a high): SMT- = primary makes a HIGHER high
    while secondary fails to (lower high) -> bearish divergence.
    """
    win_p = primary.loc[:ts].tail(200 + left + right)
    win_s = secondary.loc[:ts].tail(200 + left + right)
    if len(win_p) < left + right + 2 or len(win_s) < left + right + 2:
        return False

    piv_p = pivot_highs_lows(win_p, left, right)
    piv_s = pivot_highs_lows(win_s, left, right)

    col = "pivot_low" if direction > 0 else "pivot_high"
    confirm_col = "pl_confirmed_at" if direction > 0 else "ph_confirmed_at"

    pts_p = piv_p[piv_p[confirm_col] <= ts].dropna(subset=[col]).tail(lookback_pivots)
    pts_s = piv_s[piv_s[confirm_col] <= ts].dropna(subset=[col]).tail(lookback_pivots)
    if len(pts_p) < 2 or len(pts_s) < 2:
        return False

    p_last, p_prev = pts_p.iloc[-1], pts_p.iloc[-2]
    tol = pd.Timedelta(minutes=sync_bars * 5)
    s_candidates = pts_s[(pts_s.index >= p_last.name - tol) & (pts_s.index <= p_last.name + tol)]
    if s_candidates.empty:
        return False
    s_last = s_candidates.iloc[-1]
    s_prior = pts_s[pts_s.index < s_last.name]
    if s_prior.empty:
        return False
    s_prev = s_prior.iloc[-1]

    if direction > 0:
        primary_lower_low = p_last[col] < p_prev[col]
        secondary_higher_low = s_last[col] > s_prev[col]
        return bool(primary_lower_low and secondary_higher_low)
    else:
        primary_higher_high = p_last[col] > p_prev[col]
        secondary_lower_high = s_last[col] < s_prev[col]
        return bool(primary_higher_high and secondary_lower_high)
