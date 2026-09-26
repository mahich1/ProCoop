"""IFVG (inverted Fair Value Gap) tracker, ported from the Pine
indicator's core FVG/IFVG loop.

A 3-candle FVG is tracked as a box (top, bot, dir, created_bar). It
"inverts" - flips from support to resistance or vice versa - when a
later close moves back through its far boundary: a bullish (up-gap) box
inverts to bearish IFVG when close < its bottom; a bearish (down-gap) box
inverts to bullish IFVG when close > its top. Only the single newest
qualifying inversion per direction is kept on any given bar. Boxes expire
after `keep_age` bars unfilled.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class IfvgEvent:
    bull: bool  # True = bullish IFVG (was a failed bearish/down-gap FVG)
    top: float
    bot: float
    created_bar: int


def run_ifvg(df: pd.DataFrame, keep_age: int = 60, min_gap: float = 0.0) -> list[dict]:
    """Returns a list the same length as df; out[i] = {"bull": IfvgEvent|None,
    "bear": IfvgEvent|None} for whichever inverted on bar i."""
    n = len(df)
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()

    boxes: list[list] = []  # [top, bot, dir, created_bar]
    out = [{"bull": None, "bear": None} for _ in range(n)]

    for i in range(n):
        if i >= 2:
            bull_gap = low[i] - high[i - 2]
            bear_gap = low[i - 2] - high[i]
            if low[i] > high[i - 2] and bull_gap >= min_gap:
                boxes.append([low[i], high[i - 2], 1, i])
            if high[i] < low[i - 2] and bear_gap >= min_gap:
                boxes.append([low[i - 2], high[i], -1, i])

        bull_evt = bear_evt = None
        for z in range(len(boxes) - 1, -1, -1):
            top, bot, direction, created = boxes[z]
            too_old = (i - created) > keep_age
            bull_invert = (not too_old) and i > created and direction == -1 and close[i] > top
            bear_invert = (not too_old) and i > created and direction == 1 and close[i] < bot
            if bull_invert and bull_evt is None:
                bull_evt = IfvgEvent(True, top, bot, created)
            if bear_invert and bear_evt is None:
                bear_evt = IfvgEvent(False, top, bot, created)
            if too_old or bull_invert or bear_invert:
                boxes.pop(z)

        out[i] = {"bull": bull_evt, "bear": bear_evt}

    return out
