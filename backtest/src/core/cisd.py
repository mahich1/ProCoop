"""Persistent CISD (Change in State of Delivery) engine, ported from the
Pine indicator's `f_cisdPersistent` (V2.5.4+ "corrected persistent" mode,
the default `cisdMode`).

A CISD level is the OPEN of the first candle of a same-colour run. When
that run ends (colour flips), the level stays armed - tested on every
subsequent confirmed bar, not just the reversal bar - until a close moves
back through it (fires) or it ages out (`keep_age` bars from arming).

Bullish CISD: armed when a bearish run ends (level = that run's first
open), fires when close > level. Bearish CISD is the mirror image.
The "OB" returned alongside a fire is the last opposite-coloured candle
strictly before the firing bar (obSource = default "last opposite candle
before the move", not the one frozen at arming time).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CisdEvent:
    bull: bool
    level: float
    start_bar: int
    run_extreme: float  # low of the bearish run (for bull CISD) / high (for bear)
    ob_high: float
    ob_low: float
    arm_bar: int


def run_cisd_persistent(df: pd.DataFrame, keep_age: int = 30) -> list[CisdEvent | None]:
    """Returns a list the same length as df: events[i] is the CisdEvent
    that FIRED on bar i (bull or bear), or None. Only one can fire per
    bar in the reference script's model (ambiguous = both discarded)."""
    n = len(df)
    close = df["close"].to_numpy()
    open_ = df["open"].to_numpy()
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()

    run_dir = 0
    last_down_hi = last_down_lo = np.nan
    last_up_hi = last_up_lo = np.nan
    bear_run_open = bull_run_open = np.nan
    bear_run_low = bull_run_high = np.nan
    bear_run_bar = bull_run_bar = -1

    pend_bull_lvl = pend_bull_low = np.nan
    pend_bull_bar = pend_bull_arm = -1
    pend_bull_ob_hi = pend_bull_ob_lo = np.nan
    pend_bear_lvl = pend_bear_high = np.nan
    pend_bear_bar = pend_bear_arm = -1
    pend_bear_ob_hi = pend_bear_ob_lo = np.nan

    out: list[CisdEvent | None] = [None] * n

    for i in range(n):
        this_dir = 1 if close[i] > open_[i] else (-1 if close[i] < open_[i] else run_dir)

        if this_dir != run_dir and this_dir != 0:
            if this_dir == -1:
                if not np.isnan(bull_run_open):
                    pend_bear_lvl = bull_run_open
                    pend_bear_high = bull_run_high
                    pend_bear_bar = bull_run_bar
                    pend_bear_arm = i
                    pend_bear_ob_hi = high[i - 1] if i > 0 else np.nan
                    pend_bear_ob_lo = low[i - 1] if i > 0 else np.nan
                bear_run_open = open_[i]
                bear_run_low = low[i]
                bear_run_bar = i
            else:
                if not np.isnan(bear_run_open):
                    pend_bull_lvl = bear_run_open
                    pend_bull_low = bear_run_low
                    pend_bull_bar = bear_run_bar
                    pend_bull_arm = i
                    pend_bull_ob_hi = high[i - 1] if i > 0 else np.nan
                    pend_bull_ob_lo = low[i - 1] if i > 0 else np.nan
                bull_run_open = open_[i]
                bull_run_high = high[i]
                bull_run_bar = i
            run_dir = this_dir
        elif run_dir == -1:
            bear_run_low = low[i] if np.isnan(bear_run_low) else min(bear_run_low, low[i])
        elif run_dir == 1:
            bull_run_high = high[i] if np.isnan(bull_run_high) else max(bull_run_high, high[i])

        if not np.isnan(pend_bull_lvl) and pend_bull_arm >= 0 and i - pend_bull_arm > keep_age:
            pend_bull_lvl = np.nan
        if not np.isnan(pend_bear_lvl) and pend_bear_arm >= 0 and i - pend_bear_arm > keep_age:
            pend_bear_lvl = np.nan

        bull_fire = not np.isnan(pend_bull_lvl) and close[i] > pend_bull_lvl
        bear_fire = not np.isnan(pend_bear_lvl) and close[i] < pend_bear_lvl

        if bull_fire and not bear_fire:
            ob_hi = last_down_hi if not np.isnan(last_down_hi) else pend_bull_ob_hi
            ob_lo = last_down_lo if not np.isnan(last_down_lo) else pend_bull_ob_lo
            out[i] = CisdEvent(True, pend_bull_lvl, pend_bull_bar, pend_bull_low, ob_hi, ob_lo, pend_bull_arm)
            if pend_bull_arm < 0 or i > pend_bull_arm:
                pend_bull_lvl = np.nan
        elif bear_fire and not bull_fire:
            ob_hi = last_up_hi if not np.isnan(last_up_hi) else pend_bear_ob_hi
            ob_lo = last_up_lo if not np.isnan(last_up_lo) else pend_bear_ob_lo
            out[i] = CisdEvent(False, pend_bear_lvl, pend_bear_bar, pend_bear_high, ob_hi, ob_lo, pend_bear_arm)
            if pend_bear_arm < 0 or i > pend_bear_arm:
                pend_bear_lvl = np.nan
        # ambiguous (both fire same bar): neither is emitted, matching the
        # reference script's `cisdAmbiguous` discard.

        if close[i] < open_[i]:
            last_down_hi, last_down_lo = high[i], low[i]
        elif close[i] > open_[i]:
            last_up_hi, last_up_lo = high[i], low[i]

    return out
