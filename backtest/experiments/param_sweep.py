"""Parameter sensitivity sweep for S1/S2 on already-cached NQ/ES data.

Doesn't hit Databento - reuses whatever's in data/cache/ (run
run_backtest.py or src/data/databento_fetch.py first to populate it).

    python3 experiments/param_sweep.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.engine import simulate_single_target, simulate_two_leg
from src.backtest.metrics import summarize, trades_to_frame
from src.core.resample import to_timeframe
from src.strategies import s1_raid_smt, s2_turtle_soup

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 20)

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"

FULL_LONDON_NY = ("02:00", "11:00")  # continuous window spanning the two default killzones

S1_VARIANTS = {
    "baseline": dict(),
    "wide_kz_sync2": dict(killzones=[FULL_LONDON_NY], smt_sync_bars=2,
                           mss_lookahead=24, poi_max_age=60),
    "loose_disp": dict(body_atr_min=0.3, range_atr_min=0.8, mss_lookahead=24,
                        poi_max_age=60, killzones=[FULL_LONDON_NY], smt_sync_bars=2),
}

S2_VARIANTS = {
    "baseline": dict(),
    "wider_buffer": dict(sl_buffer_pct_atr=0.25, entry_max_age_bars=24),
    "wider_buffer_looser_disp": dict(sl_buffer_pct_atr=0.25, entry_max_age_bars=24,
                                      body_atr_min=0.35, range_atr_min=0.8),
    "tighter_zone_age": dict(zone_max_age_bars=40, sl_buffer_pct_atr=0.25,
                              entry_max_age_bars=24),
}


def run_sweep(symbol_primary="NQ", symbol_correlate="ES", start="2023-01-01", end="2025-01-01"):
    nq_m1 = pd.read_parquet(CACHE / f"{symbol_primary}_c_0_{start}_{end}.parquet")
    es_m1 = pd.read_parquet(CACHE / f"{symbol_correlate}_c_0_{start}_{end}.parquet")
    nq_m5 = to_timeframe(nq_m1, "5min")
    es_m5 = to_timeframe(es_m1, "5min")
    nq_h1 = to_timeframe(nq_m1, "1h")

    print("=== S1 sweep ===")
    rows = []
    for name, kwargs in S1_VARIANTS.items():
        sigs = s1_raid_smt.generate_signals(nq_m5, es_m5, **kwargs)
        trades = simulate_single_target(sigs, nq_m5)
        df = trades_to_frame(trades)
        summ = summarize(df) if len(df) else {"n_signals": 0}
        summ["variant"] = name
        rows.append(summ)
    print(pd.DataFrame(rows).set_index("variant").to_string())

    print("\n=== S2 sweep ===")
    rows = []
    for name, kwargs in S2_VARIANTS.items():
        sigs = s2_turtle_soup.generate_signals(nq_m5, nq_h1, **kwargs)
        trades = simulate_two_leg(sigs, nq_m5)
        df = trades_to_frame(trades)
        summ = summarize(df) if len(df) else {"n_signals": 0}
        summ["variant"] = name
        rows.append(summ)
    print(pd.DataFrame(rows).set_index("variant").to_string())


if __name__ == "__main__":
    run_sweep()
