"""Classic Donchian-channel trend-following breakout (Turtle System 1
style), on daily bars - the opposite shape from every ICT setup tested
elsewhere in this repo: slow, few trades, no fixed target, rides the
trend until it reverses.

Rules:
  - Long entry: today's close breaks above the highest high of the prior
    `n_entry` days (default 20). Short entry: mirror on the lows.
  - Initial protective stop: entry -/+ `atr_mult` x ATR(atr_len) - the
    classic Turtle "N" concept (default 2N).
  - Exit: whichever comes first -
      (a) the initial stop, or
      (b) the close crosses the OPPOSITE `n_exit`-day channel (default
          10 days) - i.e. the trend that was being ridden reverses.
  - One position at a time, no pyramiding (the original Turtle system
    adds units as the trade moves favorably - not implemented here, kept
    as a documented simplification).
  - No fixed profit target: winners are meant to run until the trend
    itself turns, which is the whole point of this style of system.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..backtest.engine import TradeResult
from ..core.structure import atr as calc_atr


def backtest(d1: pd.DataFrame, n_entry: int = 20, n_exit: int = 10,
             atr_mult: float = 2.0, atr_len: int = 20) -> list[TradeResult]:
    n = len(d1)
    high = d1["high"].to_numpy()
    low = d1["low"].to_numpy()
    close = d1["close"].to_numpy()
    idx = d1.index
    a = calc_atr(d1, atr_len).to_numpy()

    # Shifted by 1 so "today's breakout" never looks at today's own bar.
    entry_hi = pd.Series(high, index=idx).rolling(n_entry).max().shift(1).to_numpy()
    entry_lo = pd.Series(low, index=idx).rolling(n_entry).min().shift(1).to_numpy()
    exit_hi = pd.Series(high, index=idx).rolling(n_exit).max().shift(1).to_numpy()
    exit_lo = pd.Series(low, index=idx).rolling(n_exit).min().shift(1).to_numpy()

    trades: list[TradeResult] = []
    warmup = max(n_entry, n_exit, atr_len) + 1
    i = warmup
    while i < n - 1:
        if np.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue

        direction = 0
        if close[i] > entry_hi[i]:
            direction = 1
        elif close[i] < entry_lo[i]:
            direction = -1
        if direction == 0:
            i += 1
            continue

        entry_i = i + 1  # filled at next bar's open (market-style)
        if entry_i >= n:
            break
        entry_price = d1.iloc[entry_i]["open"]
        risk = atr_mult * a[i]
        stop = entry_price - direction * risk

        exit_i = None
        outcome = "timeout"
        exit_price = None
        for j in range(entry_i, n):
            bar = d1.iloc[j]
            hit_stop = bar["low"] <= stop if direction > 0 else bar["high"] >= stop
            if hit_stop:
                exit_i, outcome, exit_price = j, "stop", stop
                break
            trend_reversed = close[j] < exit_lo[j] if direction > 0 else close[j] > exit_hi[j]
            if j > entry_i and trend_reversed:
                exit_i, outcome, exit_price = j, "target", close[j]
                break
        if exit_i is None:
            exit_i, exit_price = n - 1, close[n - 1]

        r_multiple = (exit_price - entry_price) / risk * direction
        trades.append(TradeResult(
            strategy="TrendBreakout", direction=direction,
            ts_signal=idx[i], ts_fill=idx[entry_i],
            entry=entry_price, stop=stop,
            ts_exit=idx[exit_i], exit_price=exit_price,
            r_multiple=r_multiple, outcome=outcome,
            meta={"atr_at_entry": a[i]},
        ))
        i = exit_i + 1  # no overlapping positions

    return trades
