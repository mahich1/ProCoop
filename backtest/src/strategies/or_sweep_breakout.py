"""NY Opening Range (09:30-09:45) sweep-or-breakout, "first signal wins".

For each NY day: build the OR from 09:30-09:45. From 09:45 to `session_end`,
scan bars in order for the FIRST one that either:
  - closes beyond ORH/ORL -> traded as a BREAKOUT continuation in that
    direction, stop at the opposite side of the OR; or
  - wicks beyond ORH/ORL but closes back inside the OR -> traded as a
    SWEEP fade in the opposite direction, stop beyond the wick + an ATR
    buffer.
Whichever type/side happens first is the day's only trade (max one
trade/day). Fixed 2R target, entry at the next bar's open.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core import sessions
from ..core.structure import atr as calc_atr


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp
    entry: float
    stop: float
    target: float
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


def generate_signals(m5: pd.DataFrame, session_end: str = "16:00",
                      target_r: float = 2.0, sweep_buffer_atr: float = 0.1
                      ) -> list[Signal]:
    a = calc_atr(m5, 14)
    orng = sessions.session_high_low(m5, *sessions.OR_WINDOW)
    hm = m5.index.strftime("%H:%M")
    or_end = sessions.OR_WINDOW[1]

    n = len(m5)
    signals: list[Signal] = []
    traded_today: set = set()

    for i in range(30, n - 1):
        ts = m5.index[i]
        t = hm[i]
        if not (or_end <= t < session_end):
            continue

        day = pd.Timestamp(ts.date())
        if day in traded_today:
            continue
        if day not in orng.index:
            continue
        orh, orl = orng.loc[day, ["high", "low"]]
        if np.isnan(orh) or np.isnan(orl):
            continue

        bar = m5.iloc[i]
        direction = None
        kind = None
        level_hit = None

        if bar["high"] > orh:
            if bar["close"] > orh:
                direction, kind, level_hit = 1, "breakout", orh
            else:
                direction, kind, level_hit = -1, "sweep", orh
        elif bar["low"] < orl:
            if bar["close"] < orl:
                direction, kind, level_hit = -1, "breakout", orl
            else:
                direction, kind, level_hit = 1, "sweep", orl

        if direction is None:
            continue

        entry = m5.iloc[i + 1]["open"]
        if kind == "breakout":
            stop = orl if direction > 0 else orh
        else:
            buf = a.iloc[i] * sweep_buffer_atr
            stop = (bar["low"] - buf) if direction > 0 else (bar["high"] + buf)

        risk = abs(entry - stop)
        if risk <= 0:
            continue
        target = entry + direction * target_r * risk

        signals.append(Signal(
            strategy="OR_SweepBreakout", direction=direction, ts_signal=m5.index[i],
            entry=entry, stop=stop, target=target, max_entry_age_bars=1,
            meta={"kind": kind, "orh": orh, "orl": orl, "level_hit": level_hit},
        ))
        traded_today.add(day)

    return signals
