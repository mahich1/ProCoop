"""Re-run a fixed S1/S2 parameter set on a different date range/instrument
than it was picked on, with no re-tuning, to check whether an in-sample
result holds up.

Data must already be cached (data/cache/<SYM>_c_0_<start>_<end>.parquet) -
fetch it first with src/data/databento_fetch.py or run_backtest.py.

    python3 experiments/out_of_sample_check.py \
        --primary NQ --correlate ES --start 2020-01-01 --end 2023-01-01
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.engine import simulate_single_target
from src.backtest.metrics import summarize, trades_to_frame
from src.core.resample import to_timeframe
from src.strategies import s1_raid_smt

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"
REPORTS = Path(__file__).resolve().parents[1] / "reports"

FULL_LONDON_NY = ("02:00", "11:00")

# The variant picked in experiments/param_sweep.py on 2023-01-01..2025-01-01 -
# kept identical here on purpose; this script never tunes on the window it
# tests.
S1_WIDE_KZ_SYNC2 = dict(killzones=[FULL_LONDON_NY], smt_sync_bars=2,
                         mss_lookahead=24, poi_max_age=60)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", default="NQ")
    ap.add_argument("--correlate", default="ES")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    args = ap.parse_args()

    nq_m1 = pd.read_parquet(CACHE / f"{args.primary}_c_0_{args.start}_{args.end}.parquet")
    es_m1 = pd.read_parquet(CACHE / f"{args.correlate}_c_0_{args.start}_{args.end}.parquet")
    nq_m5 = to_timeframe(nq_m1, "5min")
    es_m5 = to_timeframe(es_m1, "5min")

    variants = {"baseline": dict(), "wide_kz_sync2": S1_WIDE_KZ_SYNC2}
    rows = []
    for name, kwargs in variants.items():
        sigs = s1_raid_smt.generate_signals(nq_m5, es_m5, **kwargs)
        trades = simulate_single_target(sigs, nq_m5)
        df = trades_to_frame(trades)
        summ = summarize(df) if len(df) else {"n_signals": 0}
        summ["variant"] = name
        rows.append(summ)
        out = REPORTS / f"s1_oos_{name}_{args.start}_{args.end}.csv"
        df.to_csv(out, index=False)

    print(pd.DataFrame(rows).set_index("variant").to_string())


if __name__ == "__main__":
    main()
