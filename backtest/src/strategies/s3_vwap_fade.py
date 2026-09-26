"""S3 - VWAP deviation fade (mean reversion, high win-rate target).

Mechanical, single-instrument, no cross-market SMT and no HTF zone
bookkeeping: when price stretches too far from the session VWAP and shows
an early reversal candle, fade it back toward VWAP.

Rules:
  1. Session window only (skip the first ~25min while VWAP is still
     noisy, and the last ~15min before the close).
  2. Deviation trigger: |close - VWAP| / ATR >= k_entry.
  3. Reversal confirmation on the same bar: a short needs a red candle
     (close < open), a long needs a green candle (close > open) - some
     pullback from the extreme already showing, not still accelerating.
  4. Entry at the next bar's open (market-style fill).
  5. Stop beyond the signal bar's extreme (high for shorts, low for
     longs) plus an ATR buffer.
  6. Two-leg exit: half the position at the midpoint between entry and
     VWAP (stop then moves to breakeven), the rest at VWAP itself.
     VWAP is snapshotted at signal time as a fixed target - it isn't
     re-computed bar by bar during the trade (documented simplification).
  7. At most `max_trades_per_day` signals per NY calendar day.
  8. Optional H1 trend filter (trend_htf): only fade WITH the prevailing
     H1 trend (long fades when H1 is bullish/flat, short fades when H1
     is bearish/flat) - a pullback-entry reframing rather than pure
     counter-trend fading in both directions regardless of context.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core.regime import align_to, ema_bias
from ..core.structure import atr as calc_atr
from ..core.vwap import session_vwap


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp
    entry: float
    stop: float
    target_internal: float
    target_external: float
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


def generate_signals(m5: pd.DataFrame, k_entry: float = 1.5, atr_len: int = 14,
                      stop_buffer_atr: float = 0.3, session_start: str = "09:55",
                      session_end: str = "15:45", max_trades_per_day: int = 1,
                      trend_htf: pd.DataFrame | None = None, trend_ema_len: int = 50
                      ) -> list[Signal]:
    a = calc_atr(m5, atr_len)
    vwap = session_vwap(m5)
    n = len(m5)

    trend_m5 = None
    if trend_htf is not None:
        bias_htf = ema_bias(trend_htf, trend_ema_len)
        trend_m5 = align_to(bias_htf, m5.index)

    signals: list[Signal] = []
    traded_today: dict = {}

    for i in range(atr_len + 5, n - 1):
        ts = m5.index[i]
        hm = ts.strftime("%H:%M")
        if not (session_start <= hm < session_end):
            continue

        day = ts.date()
        bar = m5.iloc[i]
        atr_v = a.iloc[i]
        vwap_v = vwap.iloc[i]
        if atr_v <= 0 or np.isnan(atr_v) or np.isnan(vwap_v):
            continue

        dev_atr = (bar["close"] - vwap_v) / atr_v

        direction = None
        if dev_atr >= k_entry and bar["close"] < bar["open"]:
            direction = -1
        elif dev_atr <= -k_entry and bar["close"] > bar["open"]:
            direction = 1
        if direction is None:
            continue

        if trend_m5 is not None:
            bias = trend_m5.iloc[i]
            if not np.isnan(bias):
                if direction > 0 and bias < 0:  # long fade against a bearish H1 trend
                    continue
                if direction < 0 and bias > 0:  # short fade against a bullish H1 trend
                    continue

        count_today = traded_today.get(day, 0)
        if count_today >= max_trades_per_day:
            continue

        entry = m5.iloc[i + 1]["open"]
        buf = atr_v * stop_buffer_atr
        stop = bar["high"] + buf if direction < 0 else bar["low"] - buf
        risk = abs(entry - stop)
        if risk <= 0:
            continue

        target_external = vwap_v
        target_internal = entry + direction * abs(target_external - entry) * 0.5
        # A fade only makes sense if VWAP is actually on the profitable side of entry.
        if direction < 0 and target_external >= entry:
            continue
        if direction > 0 and target_external <= entry:
            continue

        signals.append(Signal(
            strategy="S3", direction=direction, ts_signal=ts, entry=entry, stop=stop,
            target_internal=target_internal, target_external=target_external,
            max_entry_age_bars=1,
            meta={"vwap": vwap_v, "dev_atr": dev_atr, "atr": atr_v},
        ))
        traded_today[day] = count_today + 1

    return signals
