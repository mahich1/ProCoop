"""Asia/London liquidity sweep -> CISD -> {FVG | IFVG | MSS} -> DIRECT
market entry (no retracement wait), on M3.

User's request: only the two "major liquidity" sources (Asia and London
session H/L), not the full PDH/PDL/OR/M15 set core_reversal.py uses for
its sweep stage. Three separate confirmation modes for the second
condition after CISD - test each independently:
  - "fvg":  a fresh directional FVG completes on/after the CISD bar.
  - "ifvg": an IFVG (inverted FVG) fires in the trade direction.
  - "mss":  price closes beyond the minor swing formed since the sweep
            (a plain market-structure-shift break, no FVG required).
Entry is at the NEXT bar's open once the second condition confirms - no
50% retracement stage like core_reversal.py's default engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

from ..core import sessions
from ..core.cisd import run_cisd_persistent
from ..core.ifvg import run_ifvg
from ..core.structure import atr as calc_atr
from ..core.structure import fair_value_gaps


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp  # one bar BEFORE the fill bar (see engine trick)
    entry: float
    stop: float
    target: float
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


class _Freshness:
    def __init__(self):
        self.last_price = np.nan
        self.taken = False

    def check(self, level: float, high_side: bool, bar_high: float, bar_low: float) -> bool:
        if np.isnan(level):
            return False
        if np.isnan(self.last_price) or self.last_price != level:
            self.last_price = level
            self.taken = False
        was_fresh = not self.taken
        breached = (bar_high > level) if high_side else (bar_low < level)
        self.taken = True if breached else self.taken
        return was_fresh


def generate_signals(m3: pd.DataFrame, poi_mode: Literal["fvg", "ifvg", "mss"] = "fvg",
                      sweep_capture=("00:00", "16:00"), cisd_keep_age: int = 30,
                      confirm_lookahead: int = 6, sl_buffer_pct_atr: float = 0.1,
                      min_rr: float = 1.5, max_sweep_age: int = 60) -> list[Signal]:
    n = len(m3)
    high = m3["high"].to_numpy()
    low = m3["low"].to_numpy()
    close = m3["close"].to_numpy()
    idx = m3.index

    a = calc_atr(m3, 14)
    a_arr = a.to_numpy()
    pdh_pdl = sessions.previous_day_high_low(m3)
    asia = sessions.session_high_low(m3, *sessions.ASIA_SESSION)
    london = sessions.session_high_low(m3, *sessions.LONDON_SESSION)

    cisd_events = run_cisd_persistent(m3, cisd_keep_age)
    fvg = fair_value_gaps(m3) if poi_mode == "fvg" else None
    ifvg_events = run_ifvg(m3) if poi_mode == "ifvg" else None

    hm = idx.strftime("%H:%M")
    in_capture = (hm >= sweep_capture[0]) & (hm < sweep_capture[1])

    fresh = {"asia_lo": _Freshness(), "asia_hi": _Freshness(),
             "lon_lo": _Freshness(), "lon_hi": _Freshness()}

    signals: list[Signal] = []

    L = dict(stage=0, sweep_bar=-1, sweep_extreme=np.nan, cisd_bar=-1)
    S = dict(stage=0, sweep_bar=-1, sweep_extreme=np.nan, cisd_bar=-1)

    def day_levels(i):
        d = pd.Timestamp(idx[i].date())
        pdl = pdh_pdl.loc[d, "pdl"] if d in pdh_pdl.index else np.nan
        pdh = pdh_pdl.loc[d, "pdh"] if d in pdh_pdl.index else np.nan
        a_hi = a_lo = np.nan
        if d in asia.index and idx[i].strftime("%H:%M") >= sessions.ASIA_SESSION[1]:
            a_hi, a_lo = asia.loc[d, "high"], asia.loc[d, "low"]
        l_hi = l_lo = np.nan
        if d in london.index and idx[i].strftime("%H:%M") >= sessions.LONDON_SESSION[1]:
            l_hi, l_lo = london.loc[d, "high"], london.loc[d, "low"]
        return pdl, pdh, a_hi, a_lo, l_hi, l_lo

    for i in range(30, n - 1):
        ts = idx[i]
        bar_high, bar_low, bar_close = high[i], low[i], close[i]
        prev_low, prev_high = low[i - 1], high[i - 1]
        pdl, pdh, a_hi, a_lo, l_hi, l_lo = day_levels(i)

        def sell_sweep(name, level):
            fr = fresh[name].check(level, False, bar_high, bar_low)
            return fr and (not np.isnan(level)) and bar_low < level and bar_close > level and prev_low >= level

        def buy_sweep(name, level):
            fr = fresh[name].check(level, True, bar_high, bar_low)
            return fr and (not np.isnan(level)) and bar_high > level and bar_close < level and prev_high <= level

        sell_hit = next(((nm, lv) for nm, lv in [("AsiaLow", a_lo), ("LonLow", l_lo)]
                          if sell_sweep("asia_lo" if nm == "AsiaLow" else "lon_lo", lv)), None)
        buy_hit = next(((nm, lv) for nm, lv in [("AsiaHigh", a_hi), ("LonHigh", l_hi)]
                        if buy_sweep("asia_hi" if nm == "AsiaHigh" else "lon_hi", lv)), None)

        if in_capture[i]:
            if sell_hit is not None:
                L.update(stage=1, sweep_bar=i, sweep_extreme=bar_low, cisd_bar=-1,
                          sweep_name=sell_hit[0])
            if buy_hit is not None:
                S.update(stage=1, sweep_bar=i, sweep_extreme=bar_high, cisd_bar=-1,
                          sweep_name=buy_hit[0])

        if L["stage"] == 1 and i - L["sweep_bar"] > max_sweep_age:
            L["stage"] = 0
        if S["stage"] == 1 and i - S["sweep_bar"] > max_sweep_age:
            S["stage"] = 0

        ev = cisd_events[i]
        if L["stage"] == 1 and ev is not None and ev.bull and ev.arm_bar >= L["sweep_bar"] and i > L["sweep_bar"]:
            L["stage"] = 2
            L["cisd_bar"] = i
        if S["stage"] == 1 and ev is not None and (not ev.bull) and ev.arm_bar >= S["sweep_bar"] and i > S["sweep_bar"]:
            S["stage"] = 2
            S["cisd_bar"] = i

        for state, direction in [(L, 1), (S, -1)]:
            if state["stage"] != 2:
                continue
            if i - state["cisd_bar"] > confirm_lookahead:
                state["stage"] = 0
                continue

            confirmed = False
            if poi_mode == "fvg":
                row = fvg.iloc[i]
                if direction > 0 and not np.isnan(row["bull_fvg_ce"]):
                    confirmed = True
                elif direction < 0 and not np.isnan(row["bear_fvg_ce"]):
                    confirmed = True
            elif poi_mode == "ifvg":
                ifv = ifvg_events[i]
                if direction > 0 and ifv["bull"] is not None:
                    confirmed = True
                elif direction < 0 and ifv["bear"] is not None:
                    confirmed = True
            elif poi_mode == "mss":
                window = slice(state["sweep_bar"] + 1, i)
                if direction > 0:
                    minor = high[window].max() if i > state["sweep_bar"] + 1 else high[state["sweep_bar"]]
                    confirmed = bar_close > minor
                else:
                    minor = low[window].min() if i > state["sweep_bar"] + 1 else low[state["sweep_bar"]]
                    confirmed = bar_close < minor

            if not confirmed:
                continue

            entry = m3.iloc[i + 1]["open"]
            buf = a_arr[i] * sl_buffer_pct_atr
            stop = state["sweep_extreme"] - buf if direction > 0 else state["sweep_extreme"] + buf
            risk = abs(entry - stop)
            if risk <= 0:
                state["stage"] = 0
                continue

            opposite = pdh if direction > 0 else pdl
            candidates = [pdh, a_hi, l_hi] if direction > 0 else [pdl, a_lo, l_lo]
            candidates = [c for c in candidates if not np.isnan(c) and direction * (c - entry) >= min_rr * risk]
            target = (min(candidates) if direction > 0 else max(candidates)) if candidates \
                else entry + direction * min_rr * risk

            signals.append(Signal(
                strategy=f"SweepCISD_{poi_mode.upper()}", direction=direction, ts_signal=idx[i],
                entry=entry, stop=stop, target=target, max_entry_age_bars=1,
                meta={"sweep_name": state.get("sweep_name", ""), "poi_mode": poi_mode},
            ))
            state["stage"] = 0

    return signals
