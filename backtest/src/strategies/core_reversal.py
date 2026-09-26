"""Core Reversal engine - a faithful port of the Pine indicator's default
"moteur strict": Sweep -> CISD -> POI (IFVG or displacement OB) -> 50%
retracement -> GO, WITH the confluence gates (H1 bias, H4 veto, Premium/
Discount, minimum-RR) left at the indicator's own shipped defaults, i.e.
all OFF - they only annotate a grade (A+/A/C), they don't block a GO.

This is deliberately NOT the same code as strategies/s1_raid_smt.py (that
was a plain-English approximation written before this file was supplied).
Differences that matter, ported directly from the Pine source:
  - Sweep sources are consumed on first breach PER PRICE LEVEL (not per
    day), and the sweep bar itself must be the first bar whose low/high
    crosses the level (previous bar's low/high was still on the far
    side) - `f_firstCrossAvailable` / the `low[1] >= level` clause.
  - CISD is persistent: level = the OPEN of the first candle of the run
    that just ended, tested every closed bar (not just the reversal bar)
    until it fires or ages out - `f_cisdPersistent`.
  - POI is the CURRENT-timeframe IFVG (a violated FVG that flips
    polarity) or, in the default "IFVG ou OB" mode, the displacement
    candle that confirms CISD acting as an order block - not an HTF zone.
  - Entry/stop/target: entry = POI midpoint (poiEntryPct=50%), stop =
    the tighter-is-wider of (sweep extreme +/- buffer) and (entry -/+
    minStopAtr*ATR), target = nearest fresh external liquidity >= minRR,
    falling back to a fixed minRR target when none is fresh.
  - A pending plan is cancelled outright (no trade) if price hits its
    own stop before ever touching the entry price
    (`cancelOnStopFirst`), and separately if price closes back on the
    wrong side of the original sweep level for `reclaim_confirm_closes`
    consecutive bars (`invalidateOnReclaim`).
  - Session gating: sweeps only count inside 07:00-16:00 NY, CISD/POI
    progression only inside 08:00-16:00 NY, and the entry touch only
    inside 09:35-16:00 NY minus the last 5 minutes.

Not ported (out of scope for this pass): the HTF POI module (only used
as an optional, off-by-default confluence), the additive SMT/CISD/MSS
route, the H1->M5 Continuation engine, and the OR+FVG M1 route - these
are separate engines in the source file that don't feed this one.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..core import sessions
from ..core.cisd import run_cisd_persistent
from ..core.ifvg import run_ifvg
from ..core.structure import atr as calc_atr
from ..core.structure import displacement_leg, pivot_highs_lows

NY = "America/New_York"


@dataclass
class Signal:
    strategy: str
    direction: int  # 1 long, -1 short
    ts_signal: pd.Timestamp  # one bar BEFORE the GO/fill bar (see engine trick below)
    entry: float
    stop: float
    target_internal: float  # TP1
    target_external: float  # liquidity target (or TP2 fallback)
    max_entry_age_bars: int
    meta: dict = field(default_factory=dict)


class _Freshness:
    """Mirrors f_firstCrossAvailable: a level is usable once, until its
    price value changes (a new day's PDH, a new M15 swing, ...)."""

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


def _m15_swings(m5: pd.DataFrame, pivot_len: int = 2) -> tuple[pd.Series, pd.Series]:
    from ..core.resample import to_timeframe
    m15 = to_timeframe(m5, "15min")
    piv = pivot_highs_lows(m15, pivot_len, pivot_len)
    swing_high = piv["pivot_high"].ffill()
    swing_low = piv["pivot_low"].ffill()
    # Forward-fill onto M5 without look-ahead: a swing is only usable from
    # its OWN confirmation timestamp onward.
    hi = pd.Series(np.nan, index=m5.index)
    lo = pd.Series(np.nan, index=m5.index)
    conf_hi = piv.dropna(subset=["pivot_high"])["ph_confirmed_at"]
    conf_lo = piv.dropna(subset=["pivot_low"])["pl_confirmed_at"]
    vals_hi = piv.dropna(subset=["pivot_high"])["pivot_high"]
    vals_lo = piv.dropna(subset=["pivot_low"])["pivot_low"]
    s_hi = pd.Series(vals_hi.to_numpy(), index=conf_hi.to_numpy()).sort_index()
    s_lo = pd.Series(vals_lo.to_numpy(), index=conf_lo.to_numpy()).sort_index()
    union_hi = m5.index.union(s_hi.index)
    union_lo = m5.index.union(s_lo.index)
    hi = s_hi.reindex(union_hi).ffill().reindex(m5.index)
    lo = s_lo.reindex(union_lo).ffill().reindex(m5.index)
    return hi, lo


def generate_signals(m5: pd.DataFrame,
                      sweep_capture=("07:00", "16:00"), sequence_window=("08:00", "16:00"),
                      ny_session=("09:35", "16:00"), flat_session=("15:55", "16:00"),
                      sweep_max_age: int = 36, cisd_max_age: int = 20, cisd_keep_age: int = 30,
                      poi_max_age: int = 36, ifvg_keep_age: int = 60, ifvg_after_sweep: bool = True,
                      body_atr_min: float = 0.5, range_atr_min: float = 1.2,
                      min_stop_atr: float = 0.5, min_rr: float = 2.0, tp1_r: float = 1.5,
                      stop_buffer_ticks: float = 0.0, max_trades_per_day: int = 2,
                      invalidate_on_reclaim: bool = True, reclaim_confirm_closes: int = 3,
                      cancel_on_stop_first: bool = True) -> list[Signal]:
    n = len(m5)
    high = m5["high"].to_numpy()
    low = m5["low"].to_numpy()
    close = m5["close"].to_numpy()
    idx = m5.index

    atr_series = calc_atr(m5, 14)
    a = atr_series.to_numpy()
    pdh_pdl = sessions.previous_day_high_low(m5)
    asia = sessions.session_high_low(m5, *sessions.ASIA_SESSION)
    london = sessions.session_high_low(m5, *sessions.LONDON_SESSION)
    orng = sessions.session_high_low(m5, *sessions.OR_WINDOW)
    m15_hi, m15_lo = _m15_swings(m5)
    m15_hi_arr, m15_lo_arr = m15_hi.to_numpy(), m15_lo.to_numpy()

    cisd_events = run_cisd_persistent(m5, cisd_keep_age)
    ifvg_events = run_ifvg(m5, ifvg_keep_age)

    hm = idx.strftime("%H:%M")
    in_capture = (hm >= sweep_capture[0]) & (hm < sweep_capture[1])
    in_sequence = (hm >= sequence_window[0]) & (hm < sequence_window[1])
    in_ny = (hm >= ny_session[0]) & (hm < ny_session[1])
    in_flat = (hm >= flat_session[0]) & (hm < flat_session[1])
    sess_ok = in_ny & ~in_flat

    fresh = {name: _Freshness() for name in
             ["pdl", "asia_lo", "lon_lo", "or_lo", "m15_lo", "pdh", "asia_hi", "lon_hi", "or_hi", "m15_hi"]}

    signals: list[Signal] = []

    # Per-direction stage state. stage: 0 none, 1 sweep, 2 cisd, 3 poi/plan.
    L = dict(stage=0, sweep_bar=-1, sweep_extreme=np.nan, sweep_level=np.nan, sweep_name="",
              cisd_bar=-1, poi_bar=-1, limit=np.nan, stop=np.nan, reclaim_closes=0, day=None)
    S = dict(stage=0, sweep_bar=-1, sweep_extreme=np.nan, sweep_level=np.nan, sweep_name="",
              cisd_bar=-1, poi_bar=-1, limit=np.nan, stop=np.nan, reclaim_closes=0, day=None)

    trades_today: dict = {}
    cur_day = None

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
        o_hi = o_lo = np.nan
        if d in orng.index and idx[i].strftime("%H:%M") >= sessions.OR_WINDOW[1]:
            o_hi, o_lo = orng.loc[d, "high"], orng.loc[d, "low"]
        return pdl, pdh, a_hi, a_lo, l_hi, l_lo, o_hi, o_lo

    for i in range(2, n - 1):
        day = idx[i].date()
        if day != cur_day:
            cur_day = day
            trades_today[day] = 0

        pdl, pdh, a_hi, a_lo, l_hi, l_lo, o_hi, o_lo = day_levels(i)
        m15h, m15l = m15_hi_arr[i], m15_lo_arr[i]

        prev_low, prev_high = low[i - 1], high[i - 1]

        def sell_sweep(name, level):
            fr = fresh[name].check(level, False, high[i], low[i])
            return fr and (not np.isnan(level)) and low[i] < level and close[i] > level and prev_low >= level

        def buy_sweep(name, level):
            fr = fresh[name].check(level, True, high[i], low[i])
            return fr and (not np.isnan(level)) and high[i] > level and close[i] < level and prev_high <= level

        sell_candidates = [("PDL", pdl, sell_sweep("pdl", pdl)),
                            ("AsiaLow", a_lo, sell_sweep("asia_lo", a_lo)),
                            ("LonLow", l_lo, sell_sweep("lon_lo", l_lo)),
                            ("ORLow", o_lo, sell_sweep("or_lo", o_lo)),
                            ("M15Low", m15l, sell_sweep("m15_lo", m15l))]
        buy_candidates = [("PDH", pdh, buy_sweep("pdh", pdh)),
                           ("AsiaHigh", a_hi, buy_sweep("asia_hi", a_hi)),
                           ("LonHigh", l_hi, buy_sweep("lon_hi", l_hi)),
                           ("ORHigh", o_hi, buy_sweep("or_hi", o_hi)),
                           ("M15High", m15h, buy_sweep("m15_hi", m15h))]

        raw_sell = next(((n_, lv) for n_, lv, hit in sell_candidates if hit), None)
        raw_buy = next(((n_, lv) for n_, lv, hit in buy_candidates if hit), None)
        strict_sell = raw_sell is not None and in_capture[i]
        strict_buy = raw_buy is not None and in_capture[i]

        # ---- stage 1: sweep arms/re-arms the sequence ----
        if strict_sell:
            L.update(stage=1, sweep_bar=i, sweep_extreme=low[i], sweep_level=raw_sell[1],
                      sweep_name=raw_sell[0], cisd_bar=-1, poi_bar=-1, limit=np.nan, reclaim_closes=0)
        if strict_buy:
            S.update(stage=1, sweep_bar=i, sweep_extreme=high[i], sweep_level=raw_buy[1],
                      sweep_name=raw_buy[0], cisd_bar=-1, poi_bar=-1, limit=np.nan, reclaim_closes=0)

        # ---- expiry / reclaim invalidation ----
        if L["stage"] == 1 and i - L["sweep_bar"] > sweep_max_age:
            L["stage"] = 0
        elif L["stage"] == 2 and (i - L["sweep_bar"] > sweep_max_age or i - L["cisd_bar"] > cisd_max_age):
            L["stage"] = 0
        elif L["stage"] == 3 and i - L["poi_bar"] > poi_max_age:
            L["stage"] = 0
        if S["stage"] == 1 and i - S["sweep_bar"] > sweep_max_age:
            S["stage"] = 0
        elif S["stage"] == 2 and (i - S["sweep_bar"] > sweep_max_age or i - S["cisd_bar"] > cisd_max_age):
            S["stage"] = 0
        elif S["stage"] == 3 and i - S["poi_bar"] > poi_max_age:
            S["stage"] = 0

        if invalidate_on_reclaim and L["stage"] in (1, 2, 3) and not np.isnan(L["sweep_level"]):
            L["reclaim_closes"] = L["reclaim_closes"] + 1 if close[i] < L["sweep_level"] else 0
            if L["reclaim_closes"] >= reclaim_confirm_closes:
                L["stage"] = 0
        if invalidate_on_reclaim and S["stage"] in (1, 2, 3) and not np.isnan(S["sweep_level"]):
            S["reclaim_closes"] = S["reclaim_closes"] + 1 if close[i] > S["sweep_level"] else 0
            if S["reclaim_closes"] >= reclaim_confirm_closes:
                S["stage"] = 0

        if not in_ny[i] and in_ny[i - 1] and L["stage"] == 3:
            L["stage"] = 0
        if not in_ny[i] and in_ny[i - 1] and S["stage"] == 3:
            S["stage"] = 0

        # ---- stage 1 -> 2: CISD ----
        ev = cisd_events[i]
        if L["stage"] == 1 and in_sequence[i] and ev is not None and ev.bull and ev.arm_bar >= L["sweep_bar"] \
                and (i > L["sweep_bar"]):
            L["stage"] = 2
            L["cisd_bar"] = i
            L["cisd_ob"] = (ev.ob_low, ev.ob_high)
        if S["stage"] == 1 and in_sequence[i] and ev is not None and (not ev.bull) and ev.arm_bar >= S["sweep_bar"] \
                and (i > S["sweep_bar"]):
            S["stage"] = 2
            S["cisd_bar"] = i
            S["cisd_ob"] = (ev.ob_low, ev.ob_high)

        # ---- stage 2 -> 3: POI (IFVG first, else displacement OB) ----
        ifvg = ifvg_events[i]
        if L["stage"] == 2 and in_sequence[i] and ifvg["bull"] is not None and i > L["cisd_bar"] \
                and (not ifvg_after_sweep or ifvg["bull"].created_bar >= L["sweep_bar"]):
            L["stage"] = 3
            L["poi_bar"] = i
            L["poi_top"], L["poi_bot"] = ifvg["bull"].top, ifvg["bull"].bot
            L["poi_name"] = "IFVG"
        elif L["stage"] == 2 and in_sequence[i] and i == L["cisd_bar"] \
                and displacement_leg(m5, atr_series, i, 1, body_atr_min, range_atr_min):
            L["stage"] = 3
            L["poi_bar"] = i
            L["poi_bot"], L["poi_top"] = L["cisd_ob"]
            L["poi_name"] = "OB"

        if S["stage"] == 2 and in_sequence[i] and ifvg["bear"] is not None and i > S["cisd_bar"] \
                and (not ifvg_after_sweep or ifvg["bear"].created_bar >= S["sweep_bar"]):
            S["stage"] = 3
            S["poi_bar"] = i
            S["poi_top"], S["poi_bot"] = ifvg["bear"].top, ifvg["bear"].bot
            S["poi_name"] = "IFVG"
        elif S["stage"] == 2 and in_sequence[i] and i == S["cisd_bar"] \
                and displacement_leg(m5, atr_series, i, -1, body_atr_min, range_atr_min):
            S["stage"] = 3
            S["poi_bar"] = i
            S["poi_bot"], S["poi_top"] = S["cisd_ob"]
            S["poi_name"] = "OB"

        # ---- stage 3: compute the plan once ----
        if L["stage"] == 3 and np.isnan(L["limit"]):
            entry = L["poi_top"] - 0.5 * (L["poi_top"] - L["poi_bot"])
            structural = L["sweep_extreme"] - stop_buffer_ticks
            volatility = entry - min_stop_atr * a[i]
            stop = min(structural, volatility)
            if stop < entry:
                L["limit"], L["stop"] = entry, stop
                risk = entry - stop
                L["tp1"] = entry + tp1_r * risk
                target = _closest_above(entry, min_rr * risk, [pdh, a_hi, l_hi, o_hi, m15h])
                L["liquidity"] = target if not np.isnan(target) else entry + min_rr * risk
            else:
                L["stage"] = 0
        if S["stage"] == 3 and np.isnan(S["limit"]):
            entry = S["poi_bot"] + 0.5 * (S["poi_top"] - S["poi_bot"])
            structural = S["sweep_extreme"] + stop_buffer_ticks
            volatility = entry + min_stop_atr * a[i]
            stop = max(structural, volatility)
            if entry < stop:
                S["limit"], S["stop"] = entry, stop
                risk = stop - entry
                S["tp1"] = entry - tp1_r * risk
                target = _closest_below(entry, min_rr * risk, [pdl, a_lo, l_lo, o_lo, m15l])
                S["liquidity"] = target if not np.isnan(target) else entry - min_rr * risk
            else:
                S["stage"] = 0

        # ---- touch / stop-first / GO ----
        if L["stage"] == 3 and not np.isnan(L["limit"]) and i > L["poi_bar"]:
            touch = sess_ok[i] and low[i] <= L["limit"] <= high[i]
            stop_first = cancel_on_stop_first and low[i] <= L["stop"] and not touch
            if touch and trades_today[day] < max_trades_per_day:
                trades_today[day] += 1
                signals.append(Signal("CoreReversal", 1, idx[i - 1], L["limit"], L["stop"],
                                       L["tp1"], L["liquidity"], 1,
                                       meta={"sweep": L["sweep_name"], "poi": L["poi_name"]}))
                L["stage"] = 0
            elif touch:
                L["stage"] = 0
            elif stop_first:
                L["stage"] = 0
        if S["stage"] == 3 and not np.isnan(S["limit"]) and i > S["poi_bar"]:
            touch = sess_ok[i] and low[i] <= S["limit"] <= high[i]
            stop_first = cancel_on_stop_first and high[i] >= S["stop"] and not touch
            if touch and trades_today[day] < max_trades_per_day:
                trades_today[day] += 1
                signals.append(Signal("CoreReversal", -1, idx[i - 1], S["limit"], S["stop"],
                                       S["tp1"], S["liquidity"], 1,
                                       meta={"sweep": S["sweep_name"], "poi": S["poi_name"]}))
                S["stage"] = 0
            elif touch:
                S["stage"] = 0
            elif stop_first:
                S["stage"] = 0

    return signals


def _closest_above(entry: float, min_dist: float, candidates: list[float]) -> float:
    above = [c for c in candidates if not np.isnan(c) and c - entry >= min_dist]
    return min(above) if above else np.nan


def _closest_below(entry: float, min_dist: float, candidates: list[float]) -> float:
    below = [c for c in candidates if not np.isnan(c) and entry - c >= min_dist]
    return max(below) if below else np.nan
