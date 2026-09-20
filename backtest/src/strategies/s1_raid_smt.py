"""S1 — Raid HTF + SMT + displacement (la plus forte).

Accumulation -> prise de PDH/PDL ou Asian High/Low en killzone -> SMT sur
l'indice correle au moment exact du raid -> displacement violent avec MSS
-> FVG laisse -> retracement en CE -> continuation vers la liquidite
opposee.

Works on M5 bars for the primary/correlated instrument, with daily and
Asia-session levels computed from the same instrument's own history.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core import sessions, smt as smt_mod
from ..core.structure import atr as calc_atr
from ..core.structure import displacement_leg, fair_value_gaps, pivot_highs_lows

KILLZONES = [sessions.LONDON_KILLZONE, sessions.NY_AM_KILLZONE]


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp
    ts_raid: pd.Timestamp
    entry: float
    stop: float
    target: float
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


def _in_any_killzone(ts: pd.Timestamp) -> bool:
    hm = ts.strftime("%H:%M")
    for start, end in KILLZONES:
        if start <= hm < end:
            return True
    return False


def generate_signals(m5: pd.DataFrame, es_m5: pd.DataFrame,
                      poi_max_age: int = 36, mss_lookahead: int = 12,
                      body_atr_min: float = 0.5, range_atr_min: float = 1.2,
                      min_rr: float = 1.5, sl_buffer_pct_atr: float = 0.1,
                      smt_left: int = 3, smt_right: int = 1,
                      smt_sync_bars: int = 1) -> list[Signal]:
    """m5, es_m5: DatetimeIndex in America/New_York, columns open/high/low/close.
    Both must cover the same date range (SMT is matched by timestamp)."""
    a = calc_atr(m5, 14)
    pdh_pdl = sessions.previous_day_high_low(m5)
    asia = sessions.session_high_low(m5, *sessions.ASIA_SESSION)
    fvg = fair_value_gaps(m5)

    signals: list[Signal] = []
    n = len(m5)

    for i in range(30, n - 1):
        ts = m5.index[i]
        if not _in_any_killzone(ts):
            continue

        day = pd.Timestamp(ts.date())
        if day not in pdh_pdl.index or pd.isna(pdh_pdl.loc[day, "pdl"]):
            continue
        pdl = pdh_pdl.loc[day, "pdl"]
        pdh = pdh_pdl.loc[day, "pdh"]

        prev_day_key = pd.Timestamp(ts.date())
        asia_hi = asia_lo = np.nan
        if prev_day_key in asia.index:
            asia_hi, asia_lo = asia.loc[prev_day_key, ["high", "low"]]

        bar = m5.iloc[i]

        for direction, level, level_name in [
            (1, pdl, "PDL"), (1, asia_lo, "AsiaLow"),
            (-1, pdh, "PDH"), (-1, asia_hi, "AsiaHigh"),
        ]:
            if np.isnan(level):
                continue
            swept = (bar["low"] < level < bar["close"]) if direction > 0 else \
                    (bar["high"] > level > bar["close"])
            if not swept:
                continue

            if not smt_mod.smt_at(m5, es_m5, ts, direction, smt_left, smt_right, smt_sync_bars):
                continue

            raid_extreme = bar["low"] if direction > 0 else bar["high"]

            disp_i = None
            for j in range(i + 1, min(i + 1 + mss_lookahead, n)):
                if displacement_leg(m5, a, j, direction, body_atr_min, range_atr_min):
                    disp_i = j
                    break
            if disp_i is None:
                continue

            recent = m5.iloc[i + 1: disp_i]
            if direction > 0:
                minor_swing = recent["high"].max() if len(recent) else bar["high"]
                mss_ok = m5.iloc[disp_i]["close"] > minor_swing
            else:
                minor_swing = recent["low"].min() if len(recent) else bar["low"]
                mss_ok = m5.iloc[disp_i]["close"] < minor_swing
            if not mss_ok:
                continue

            fvg_row = fvg.iloc[disp_i]
            if direction > 0 and not np.isnan(fvg_row["bull_fvg_ce"]):
                ce = fvg_row["bull_fvg_ce"]
                fvg_low, fvg_high = fvg_row["bull_fvg_low"], fvg_row["bull_fvg_high"]
            elif direction < 0 and not np.isnan(fvg_row["bear_fvg_ce"]):
                ce = fvg_row["bear_fvg_ce"]
                fvg_low, fvg_high = fvg_row["bear_fvg_low"], fvg_row["bear_fvg_high"]
            else:
                continue

            buf = a.iloc[disp_i] * sl_buffer_pct_atr
            stop = raid_extreme - buf if direction > 0 else raid_extreme + buf
            entry = ce
            risk = abs(entry - stop)
            if risk <= 0:
                continue

            opposite = pdh if direction > 0 else pdl
            if np.isnan(opposite) or abs(opposite - entry) / risk < min_rr:
                target = entry + direction * min_rr * risk
            else:
                target = opposite

            signals.append(Signal(
                strategy="S1", direction=direction, ts_signal=m5.index[disp_i],
                ts_raid=ts, entry=entry, stop=stop, target=target,
                max_entry_age_bars=poi_max_age,
                meta={"level_name": level_name, "level": level,
                      "fvg_low": fvg_low, "fvg_high": fvg_high,
                      "raid_extreme": raid_extreme},
            ))
            break  # one raid-driven signal per bar is enough

    return signals
