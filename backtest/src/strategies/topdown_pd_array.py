"""Top-down HTF PD array model, from the user's 5-step screenshot:

  1. Identify D1 + H4 + H1 structure/bias - all three must agree.
  2. Identify the HTF PD array (here: an unmitigated H4 FVG - NWOG/Order
     Block/Breaker Block are the same "zone" idea, not separately
     implemented in this pass).
  3. A time-based liquidity sweep (London session) that tests the zone -
     price runs into the HTF PD array and sweeps a local high/low
     formed since it got there.
  4. A 5-minute BOS + FVG in the direction of the HTF bias.
  5. Entry on the M5 FVG, fixed 1:3 R target, partial at 1R with stop
     moved to breakeven (`simulate_two_leg`'s target_internal/external).

Reuses s2_turtle_soup's HTF-zone bookkeeping (entry/sweep-inside-zone
logic is the same shape as "Turtle Soup in a PD array") but adds the
D1/H4/H1 bias requirement as a hard gate (not optional, per the model)
and replaces "nearest liquidity" targeting with a fixed 1:3 RR.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core import sessions
from ..core.regime import align_to, ema_bias
from ..core.structure import atr as calc_atr
from ..core.structure import displacement_leg, fair_value_gaps
from .s2_turtle_soup import _htf_zones


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp
    ts_sweep: pd.Timestamp
    entry: float
    stop: float
    target_internal: float  # 1R partial
    target_external: float  # 3R final
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


def generate_signals(m5: pd.DataFrame, d1_htf: pd.DataFrame, h4_htf: pd.DataFrame,
                      h1_htf: pd.DataFrame, bias_ema_len: int = 20,
                      zone_max_age_bars: int = 80, mss_lookahead: int = 12,
                      body_atr_min: float = 0.5, range_atr_min: float = 1.0,
                      sl_buffer_pct_atr: float = 0.1, entry_max_age_bars: int = 12,
                      rr_final: float = 3.0, rr_partial: float = 1.0,
                      session_window=sessions.LONDON_SESSION) -> list[Signal]:
    a = calc_atr(m5, 14)
    fvg_m5 = fair_value_gaps(m5)

    bias_d1 = align_to(ema_bias(d1_htf, bias_ema_len), m5.index)
    bias_h4 = align_to(ema_bias(h4_htf, bias_ema_len), m5.index)
    bias_h1 = align_to(ema_bias(h1_htf, bias_ema_len), m5.index)
    aligned = np.where((bias_d1 == bias_h4) & (bias_h4 == bias_h1), bias_d1, np.nan)

    all_zones = _htf_zones(h4_htf, zone_max_age_bars)
    max_age_m5_bars = zone_max_age_bars * 48  # H4 bars -> approx M5 bars

    hm = m5.index.strftime("%H:%M")
    in_session = (hm >= session_window[0]) & (hm < session_window[1])

    signals: list[Signal] = []
    n = len(m5)
    zone_ptr = 0
    active_zones: list[dict] = []

    for i in range(30, n - 1):
        ts = m5.index[i]
        bar = m5.iloc[i]
        prev_close = m5.iloc[i - 1]["close"]
        htf_bias = aligned[i]

        while zone_ptr < len(all_zones) and all_zones[zone_ptr]["confirmed_at"] < ts:
            all_zones[zone_ptr]["_confirmed_i"] = m5.index.searchsorted(all_zones[zone_ptr]["confirmed_at"])
            active_zones.append(all_zones[zone_ptr])
            zone_ptr += 1
        if active_zones:
            active_zones = [z for z in active_zones
                             if not z["mitigated"] and (i - z["_confirmed_i"]) <= max_age_m5_bars]

        if np.isnan(htf_bias):
            continue

        for z in active_zones:
            zlow, zhigh = z["low"], z["high"]
            inside_now = zlow <= bar["close"] <= zhigh or zlow <= bar["high"] <= zhigh or zlow <= bar["low"] <= zhigh
            was_outside = not (zlow <= prev_close <= zhigh)

            if inside_now and was_outside and "entered_at" not in z:
                if not in_session[i]:
                    continue
                # HTF bearish -> want price rallying UP into the zone (short setup).
                # HTF bullish -> want price selling DOWN into the zone (long setup).
                approach_up = prev_close < zlow
                approach_down = prev_close > zhigh
                wanted = "up" if htf_bias < 0 else "down"
                actual = "up" if approach_up else ("down" if approach_down else None)
                if actual != wanted:
                    continue
                z["entered_at"] = i
                z["active_high"] = bar["high"]
                z["active_low"] = bar["low"]
                z["approach"] = actual
                z["bias_at_entry"] = htf_bias
                continue

            if "entered_at" not in z:
                continue

            still_inside = zlow <= bar["high"] and bar["low"] <= zhigh
            if not still_inside:
                z["mitigated"] = True
                continue

            approach = z.get("approach")
            if approach == "up":
                local_high = z["active_high"]
                swept = bar["high"] > local_high and bar["close"] < local_high and bar["high"] <= zhigh * 1.0015
                z["active_high"] = max(z["active_high"], bar["high"])
                if not swept:
                    continue
                direction = -1
                sweep_extreme = bar["high"]
            elif approach == "down":
                local_low = z["active_low"]
                swept = bar["low"] < local_low and bar["close"] > local_low and bar["low"] >= zlow * 0.9985
                z["active_low"] = min(z["active_low"], bar["low"])
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

            signals.append(Signal(
                strategy="TopDownPD", direction=direction, ts_signal=m5.index[disp_i],
                ts_sweep=ts, entry=entry, stop=stop,
                target_internal=entry + direction * rr_partial * risk,
                target_external=entry + direction * rr_final * risk,
                max_entry_age_bars=entry_max_age_bars,
                meta={"zone_kind": z["kind"], "zone_low": zlow, "zone_high": zhigh,
                      "htf_bias": int(htf_bias), "sweep_extreme": sweep_extreme},
            ))
            z["mitigated"] = True

    return signals
