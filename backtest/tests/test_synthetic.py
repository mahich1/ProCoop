"""Sanity tests on hand-crafted synthetic OHLCV data. Real Databento data
was not reachable from this environment when this was written (network
policy blocks hist.databento.com), so these are the only tests that have
actually been run end-to-end - they exist to catch wiring/logic bugs in
the detection pipeline before it's ever pointed at real NQ/ES data.

Run: python3 tests/test_synthetic.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd

from src.core.resample import to_timeframe
from src.core.structure import atr, fair_value_gaps, pivot_highs_lows
from src.backtest.engine import simulate_single_target, simulate_two_leg
from src.strategies import s1_raid_smt, s2_turtle_soup

NY = "America/New_York"


def _set_bar(df, ts, o=None, h=None, l=None, c=None):
    ts = pd.Timestamp(ts, tz=NY) if isinstance(ts, str) else pd.Timestamp(ts)
    if o is not None:
        df.loc[ts, "open"] = o
    if h is not None:
        df.loc[ts, "high"] = h
    if l is not None:
        df.loc[ts, "low"] = l
    if c is not None:
        df.loc[ts, "close"] = c


# --------------------------------------------------------------------------
# Core primitives
# --------------------------------------------------------------------------

def test_pivot_highs_lows():
    idx = pd.date_range("2024-01-01", periods=9, freq="5min", tz=NY)
    low = [10, 10, 10, 5, 10, 10, 10, 10, 10]
    high = [11, 11, 11, 11, 11, 11, 15, 11, 11]
    df = pd.DataFrame({"open": low, "close": low, "low": low, "high": high}, index=idx)
    piv = pivot_highs_lows(df, left=2, right=2)
    assert piv["pivot_low"].iloc[3] == 5
    assert piv["pl_confirmed_at"].iloc[3] == idx[5]
    assert piv["pivot_high"].iloc[6] == 15
    assert piv["ph_confirmed_at"].iloc[6] == idx[8]
    print("test_pivot_highs_lows OK")


def test_fair_value_gaps():
    idx = pd.date_range("2024-01-01", periods=3, freq="5min", tz=NY)
    df = pd.DataFrame({
        "open": [100.0, 101.0, 103.0],
        "close": [100.5, 101.5, 103.5],
        "high": [100.8, 101.8, 103.8],
        "low": [99.8, 101.0, 103.0],
    }, index=idx)
    fvg = fair_value_gaps(df)
    assert fvg["bull_fvg_low"].iloc[2] == 100.8
    assert fvg["bull_fvg_high"].iloc[2] == 103.0
    print("test_fair_value_gaps OK")


def test_atr_positive():
    idx = pd.date_range("2024-01-01", periods=20, freq="5min", tz=NY)
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.1, 20))
    df = pd.DataFrame({
        "open": close, "close": close,
        "high": close + 0.1, "low": close - 0.1,
    }, index=idx)
    a = atr(df, 14)
    assert (a.dropna() > 0).all()
    print("test_atr_positive OK")


# --------------------------------------------------------------------------
# S1 — raid + SMT + displacement, end to end
# --------------------------------------------------------------------------

def _build_s1_data():
    idx = pd.date_range("2024-01-01 00:00", "2024-01-03 00:00", freq="5min",
                         tz=NY, inclusive="left")
    n = len(idx)
    nq_close = 100.0 + 0.00002 * np.arange(n)
    es_close = 4000.0 + 0.0008 * np.arange(n)
    nq = pd.DataFrame({"open": nq_close - 0.00002, "high": nq_close + 0.02,
                        "low": nq_close - 0.02, "close": nq_close}, index=idx)
    es = pd.DataFrame({"open": es_close - 0.0008, "high": es_close + 0.5,
                        "low": es_close - 0.5, "close": es_close}, index=idx)

    for ts in nq.loc["2024-01-01"].index:
        _set_bar(nq, ts, o=100.0, h=100.15, l=99.9, c=100.05)  # -> PDL=99.9, PDH=100.15

    # NQ: pivot low #1 (higher) 01:30, pivot low #2 (lower) 01:55 -> lower low
    _set_bar(nq, "2024-01-02 01:30", o=100.0, h=100.02, l=99.85, c=99.95)
    _set_bar(nq, "2024-01-02 01:55", o=99.99, h=100.0, l=99.70, c=99.95)
    # ES: pivot low #1 (lower) 01:30, pivot low #2 (HIGHER) 01:55 -> bullish SMT divergence
    _set_bar(es, "2024-01-02 01:30", o=4000.0, h=4001.0, l=3995.0, c=3999.0)
    _set_bar(es, "2024-01-02 01:55", o=3999.8, h=4001.0, l=3998.0, c=4000.0)

    # Raid: sweep PDL (99.9) at 02:20, inside the London killzone, close back above
    _set_bar(nq, "2024-01-02 02:20", o=99.97, h=99.97, l=99.80, c=99.95)
    # Displacement + bull FVG at 02:30 (needs low[02:30] > high[02:20])
    _set_bar(nq, "2024-01-02 02:25", o=99.95, h=99.97, l=99.94, c=99.96)
    _set_bar(nq, "2024-01-02 02:30", o=99.99, h=103.5, l=99.99, c=103.3)
    # Retracement back into the FVG CE, then continuation toward target
    _set_bar(nq, "2024-01-02 02:35", o=103.3, h=103.3, l=100.5, c=100.6)
    _set_bar(nq, "2024-01-02 02:40", o=100.6, h=100.6, l=99.90, c=99.99)
    _set_bar(nq, "2024-01-02 02:45", o=99.99, h=100.2, l=99.97, c=100.1)
    _set_bar(nq, "2024-01-02 02:50", o=100.1, h=100.8, l=100.05, c=100.7)
    _set_bar(nq, "2024-01-02 02:55", o=100.7, h=101.0, l=100.6, c=100.9)
    return nq, es


def test_s1_signal_and_fill():
    nq, es = _build_s1_data()
    sigs = s1_raid_smt.generate_signals(nq, es)
    assert len(sigs) >= 1, "expected at least one S1 signal on the engineered raid"
    sig = sigs[0]
    assert sig.direction == 1
    assert sig.meta["level_name"] == "PDL"
    assert sig.stop < sig.entry < sig.target

    trades = simulate_single_target(sigs, nq)
    assert trades[0].outcome in ("target", "stop", "timeout")
    assert trades[0].ts_fill is not None, "engineered retracement should fill the entry"
    print("test_s1_signal_and_fill OK:", sig.direction, sig.entry, sig.stop, sig.target,
          "->", trades[0].outcome, trades[0].r_multiple)


# --------------------------------------------------------------------------
# S2 — Turtle Soup in an HTF PD array, end to end
# --------------------------------------------------------------------------

def _build_s2_data():
    idx = pd.date_range("2024-01-01 00:00", "2024-01-03 00:00", freq="5min",
                         tz=NY, inclusive="left")
    n = len(idx)
    o = np.full(n, 100.0)
    h = np.full(n, 100.05)
    l = np.full(n, 99.95)
    c = np.full(n, 100.0)

    def set_range(t0, t1, o_, h_, l_, c_):
        mask = (idx >= pd.Timestamp(t0, tz=NY)) & (idx < pd.Timestamp(t1, tz=NY))
        o[mask], h[mask], l[mask], c[mask] = o_, h_, l_, c_

    set_range("2024-01-01 00:00", "2024-01-01 08:00", 100.0, 100.05, 99.95, 100.0)
    set_range("2024-01-01 08:00", "2024-01-01 09:00", 100.35, 100.5, 100.3, 100.4)  # H1 candle A

    mask3 = (idx >= pd.Timestamp("2024-01-01 09:00", tz=NY)) & (idx < pd.Timestamp("2024-01-01 10:00", tz=NY))
    n3 = mask3.sum()
    drop_close = np.linspace(100.3, 98.2, n3)
    c[mask3] = drop_close
    o[mask3] = np.r_[100.3, drop_close[:-1]]
    h[mask3] = np.maximum(o[mask3], c[mask3]) + 0.05
    l[mask3] = np.minimum(o[mask3], c[mask3]) - 0.05  # H1 candle B (impulsive drop)

    set_range("2024-01-01 10:00", "2024-01-01 11:00", 97.7, 97.9, 97.0, 97.3)  # H1 candle C -> bear FVG (97.9, 100.3)
    set_range("2024-01-01 11:00", "2024-01-02 12:00", 96.5, 96.55, 96.45, 96.5)  # drift/consolidate

    mask6 = (idx >= pd.Timestamp("2024-01-02 12:00", tz=NY)) & (idx < pd.Timestamp("2024-01-02 15:00", tz=NY))
    n6 = mask6.sum()
    rally_close = np.linspace(96.5, 97.9, n6)
    c[mask6] = rally_close
    o[mask6] = np.r_[96.5, rally_close[:-1]]
    h[mask6] = np.maximum(o[mask6], c[mask6]) + 0.03
    l[mask6] = np.minimum(o[mask6], c[mask6]) - 0.03  # rally back up toward the zone

    df = pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=idx)

    # Rally into the zone, local high forms and gets swept, bearish displacement + MSS, M5 FVG
    _set_bar(df, "2024-01-02 14:55", o=97.85, h=97.95, l=97.8, c=97.9)
    _set_bar(df, "2024-01-02 15:00", o=97.9, h=98.1, l=97.9, c=98.05)    # enters zone
    _set_bar(df, "2024-01-02 15:05", o=98.05, h=98.3, l=98.0, c=98.2)
    _set_bar(df, "2024-01-02 15:10", o=98.2, h=98.5, l=98.15, c=98.3)
    _set_bar(df, "2024-01-02 15:15", o=98.3, h=98.6, l=98.4, c=98.5)     # local high 98.6
    _set_bar(df, "2024-01-02 15:20", o=98.5, h=98.9, l=98.3, c=98.4)    # sweeps 98.6, closes back below
    _set_bar(df, "2024-01-02 15:25", o=98.3, h=98.35, l=96.4, c=96.5)   # bearish displacement + FVG
    _set_bar(df, "2024-01-02 15:30", o=96.5, h=97.0, l=96.4, c=96.9)
    _set_bar(df, "2024-01-02 15:35", o=96.9, h=97.6, l=96.9, c=97.5)
    _set_bar(df, "2024-01-02 15:40", o=97.5, h=98.4, l=97.5, c=98.35)   # retraces into the FVG CE
    _set_bar(df, "2024-01-02 15:45", o=98.35, h=98.4, l=97.0, c=97.1)
    return df


def test_s2_signal_and_fill():
    m5 = _build_s2_data()
    h1 = to_timeframe(m5, "1h")
    sigs = s2_turtle_soup.generate_signals(m5, h1)
    assert len(sigs) >= 1, "expected at least one S2 signal on the engineered turtle soup"
    sig = sigs[0]
    assert sig.direction == -1
    assert sig.meta["zone_kind"] == "bear"
    assert sig.entry < sig.stop, "short: entry must be below stop"

    trades = simulate_two_leg(sigs, m5)
    assert trades[0].ts_fill is not None, "engineered retracement should fill the entry"
    print("test_s2_signal_and_fill OK:", sig.direction, sig.entry, sig.stop,
          sig.target_internal, sig.target_external, "->", trades[0].outcome, trades[0].r_multiple)


if __name__ == "__main__":
    test_pivot_highs_lows()
    test_fair_value_gaps()
    test_atr_positive()
    test_s1_signal_and_fill()
    test_s2_signal_and_fill()
    print("\nAll synthetic sanity tests passed.")
