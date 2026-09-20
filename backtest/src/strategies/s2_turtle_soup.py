"""S2 — Turtle Soup dans un PD Array HTF.

Prix monte chercher un OB/FVG H1-H4 -> sweep des highs a l'interieur de
cette zone -> displacement baissier + MSS M5 -> entree sur FVG M1/M5 ->
cible : low interne puis externe. (Mirrored for the long side.)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core import sessions
from ..core.structure import atr as calc_atr
from ..core.structure import displacement_leg, fair_value_gaps


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp
    ts_sweep: pd.Timestamp
    entry: float
    stop: float
    target_internal: float
    target_external: float
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


def _htf_zones(htf: pd.DataFrame, max_age_bars: int) -> list[dict]:
    """Unmitigated H1/H4 FVG zones: kept only until price closes back
    through them (first mitigation), which is exactly the event S2 is
    trying to catch on the lower timeframe, so here we just track age.
    """
    fvg = fair_value_gaps(htf)
    zones = []
    n = len(htf)
    for i in range(n):
        ts = htf.index[i]
        # The HTF candle that creates the FVG is only fully known once it
        # closes, i.e. once the *next* HTF bar opens - using formed_at
        # itself would let lower-timeframe bars "see" the zone while that
        # candle is still forming (look-ahead).
        confirmed_at = htf.index[i + 1] if i + 1 < n else ts
        if not np.isnan(fvg["bull_fvg_low"].iloc[i]):
            zones.append({"kind": "bull", "low": fvg["bull_fvg_low"].iloc[i],
                          "high": fvg["bull_fvg_high"].iloc[i], "formed_at": ts,
                          "confirmed_at": confirmed_at, "formed_i": i, "mitigated": False})
        if not np.isnan(fvg["bear_fvg_low"].iloc[i]):
            zones.append({"kind": "bear", "low": fvg["bear_fvg_low"].iloc[i],
                          "high": fvg["bear_fvg_high"].iloc[i], "formed_at": ts,
                          "confirmed_at": confirmed_at, "formed_i": i, "mitigated": False})
    return zones


def generate_signals(m5: pd.DataFrame, htf: pd.DataFrame,
                      zone_max_age_bars: int = 80, mss_lookahead: int = 12,
                      body_atr_min: float = 0.5, range_atr_min: float = 1.0,
                      sl_buffer_pct_atr: float = 0.1,
                      entry_max_age_bars: int = 12) -> list[Signal]:
    a = calc_atr(m5, 14)
    fvg_m5 = fair_value_gaps(m5)
    pdh_pdl = sessions.previous_day_high_low(m5)
    zones = _htf_zones(htf, zone_max_age_bars)

    signals: list[Signal] = []
    n = len(m5)
    active_high_since_entry: dict[int, float] = {}
    active_low_since_entry: dict[int, float] = {}
    entered_zone_at: dict[int, int] = {}

    for i in range(30, n - 1):
        ts = m5.index[i]
        bar = m5.iloc[i]
        prev_close = m5.iloc[i - 1]["close"]

        for zi, z in enumerate(zones):
            if z["mitigated"] or ts <= z["confirmed_at"]:
                continue
            age_bars = i - m5.index.searchsorted(z["confirmed_at"])
            if age_bars > zone_max_age_bars * 12:  # H1 bars -> approx M5 bars
                z["mitigated"] = True
                continue

            zlow, zhigh = z["low"], z["high"]
            inside_now = zlow <= bar["close"] <= zhigh or zlow <= bar["high"] <= zhigh or zlow <= bar["low"] <= zhigh
            was_outside = not (zlow <= prev_close <= zhigh)

            if inside_now and was_outside and zi not in entered_zone_at:
                entered_zone_at[zi] = i
                active_high_since_entry[zi] = bar["high"]
                active_low_since_entry[zi] = bar["low"]
                approach_up = prev_close < zlow
                approach_down = prev_close > zhigh
                z["approach"] = "up" if approach_up else ("down" if approach_down else None)
                continue

            if zi not in entered_zone_at:
                continue

            still_inside = zlow <= bar["high"] and bar["low"] <= zhigh
            if not still_inside:
                z["mitigated"] = True
                entered_zone_at.pop(zi, None)
                continue

            approach = z.get("approach")
            if approach == "up":
                local_high = active_high_since_entry[zi]
                swept = bar["high"] > local_high and bar["close"] < local_high and bar["high"] <= zhigh * 1.0015
                active_high_since_entry[zi] = max(active_high_since_entry[zi], bar["high"])
                if not swept:
                    continue
                direction = -1
                sweep_extreme = bar["high"]
            elif approach == "down":
                local_low = active_low_since_entry[zi]
                swept = bar["low"] < local_low and bar["close"] > local_low and bar["low"] >= zlow * 0.9985
                active_low_since_entry[zi] = min(active_low_since_entry[zi], bar["low"])
                if not swept:
                    continue
                direction = 1
                sweep_extreme = bar["low"]
            else:
                continue

            disp_i = None
            for j in range(i + 1, min(i + 1 + mss_lookahead, n)):
                if displacement_leg(m5, a, j, direction, body_atr_min, range_atr_min):
                    disp_i = j
                    break
            if disp_i is None:
                continue

            recent = m5.iloc[i + 1: disp_i]
            if direction < 0:
                minor_swing = recent["low"].min() if len(recent) else bar["low"]
                mss_ok = m5.iloc[disp_i]["close"] < minor_swing
            else:
                minor_swing = recent["high"].max() if len(recent) else bar["high"]
                mss_ok = m5.iloc[disp_i]["close"] > minor_swing
            if not mss_ok:
                continue

            fvg_row = fvg_m5.iloc[disp_i]
            if direction < 0 and not np.isnan(fvg_row["bear_fvg_ce"]):
                ce = fvg_row["bear_fvg_ce"]
            elif direction > 0 and not np.isnan(fvg_row["bull_fvg_ce"]):
                ce = fvg_row["bull_fvg_ce"]
            else:
                continue

            buf = a.iloc[disp_i] * sl_buffer_pct_atr
            stop = sweep_extreme + buf if direction < 0 else sweep_extreme - buf
            entry = ce
            risk = abs(entry - stop)
            if risk <= 0:
                continue

            window_start = entered_zone_at[zi]
            since_entry = m5.iloc[window_start:disp_i + 1]
            if direction < 0:
                internal_target = since_entry["low"].min()
            else:
                internal_target = since_entry["high"].max()

            day = pd.Timestamp(ts.date())
            ext = np.nan
            if day in pdh_pdl.index:
                ext = pdh_pdl.loc[day, "pdl"] if direction < 0 else pdh_pdl.loc[day, "pdh"]
            external_target = ext if not np.isnan(ext) else (entry + direction * 3 * risk)

            signals.append(Signal(
                strategy="S2", direction=direction, ts_signal=m5.index[disp_i],
                ts_sweep=ts, entry=entry, stop=stop,
                target_internal=internal_target, target_external=external_target,
                max_entry_age_bars=entry_max_age_bars,
                meta={"zone_kind": z["kind"], "zone_low": zlow, "zone_high": zhigh,
                      "zone_formed_at": z["formed_at"], "sweep_extreme": sweep_extreme},
            ))
            z["mitigated"] = True
            entered_zone_at.pop(zi, None)

    return signals
