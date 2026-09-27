"""Does the sweep -> CISD -> {FVG|IFVG} -> direct-entry edge (built and
validated on Asia+London, see README.md) generalise to the indicator's
other sweep sources - PDH/PDL, the finalised NY Opening Range, and the
M15 swing high/low?

Same discipline as every other test in this repo: parameters (cisd_keep_age,
confirm_lookahead, sl_buffer_pct_atr, min_rr, max_sweep_age) are left at
the exact values already tuned for Asia+London - nothing is re-tuned per
source here, this is purely "does the same recipe transfer", not a fresh
optimisation. Design window 2023-01-01 -> 2025-01-01, validated unchanged
on 2020-01-01 -> 2023-01-01. Reuses the NQ M1 parquet cache already on
disk - no extra Databento cost.

    python3 experiments/sweep_source_check.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.engine import simulate_single_target
from src.backtest.metrics import streaks, summarize, trades_to_frame
from src.core.resample import to_timeframe
from src.strategies import sweep_cisd_direct

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"
REPORTS = Path(__file__).resolve().parents[1] / "reports"

WINDOWS = [("2023-01-01_2025-01-01", "in-sample"), ("2020-01-01_2023-01-01", "out-of-sample")]
SOURCE_SETS = {
    "pdh_pdl": ("pdh_pdl",),
    "or": ("or",),
    "m15": ("m15",),
    "all5": ("pdh_pdl", "asia", "lon", "or", "m15"),
}
POI_MODES = ["fvg", "ifvg"]


def main():
    rows = []
    for src_label, sources in SOURCE_SETS.items():
        for poi_mode in POI_MODES:
            combined = []
            per_window = {}
            for win_label, tag in WINDOWS:
                m1 = pd.read_parquet(CACHE / f"NQ_c_0_{win_label}.parquet")
                m3 = to_timeframe(m1, "3min")
                sigs = sweep_cisd_direct.generate_signals(m3, poi_mode=poi_mode, sweep_sources=sources)
                trades = simulate_single_target(sigs, m3)
                df = trades_to_frame(trades)
                df = df[df["outcome"] != "no_fill"]
                summ = summarize(df)
                per_window[tag] = summ
                combined.append(df)
                out_csv = REPORTS / f"sweepcisd_{src_label}_{poi_mode}_{win_label}.csv"
                df.to_csv(out_csv, index=False)

            all_df = pd.concat(combined, ignore_index=True)
            comb_summ = summarize(all_df)
            if comb_summ.get("n_filled", 0) == 0:
                rows.append({"source": src_label, "poi_mode": poi_mode, "n_is": 0, "pf_is": float("nan"),
                             "n_oos": 0, "pf_oos": float("nan"), "n_combined": 0, "pf_combined": float("nan"),
                             "total_r": 0.0, "max_dd_r": 0.0, "max_win_streak": 0, "max_loss_streak": 0})
                continue
            max_win, max_loss = streaks(all_df.sort_values("ts_fill")["r_multiple"])
            rows.append({
                "source": src_label, "poi_mode": poi_mode,
                "n_is": per_window["in-sample"].get("n_filled", 0),
                "pf_is": per_window["in-sample"].get("profit_factor", float("nan")),
                "n_oos": per_window["out-of-sample"].get("n_filled", 0),
                "pf_oos": per_window["out-of-sample"].get("profit_factor", float("nan")),
                "n_combined": comb_summ["n_filled"], "pf_combined": comb_summ["profit_factor"],
                "total_r": comb_summ["total_r"], "max_dd_r": comb_summ["max_drawdown_r"],
                "max_win_streak": max_win, "max_loss_streak": max_loss,
            })

    out = pd.DataFrame(rows)
    pd.set_option("display.width", 160)
    print(out.to_string(index=False))
    out.to_csv(REPORTS / "sweep_source_check_summary.csv", index=False)


if __name__ == "__main__":
    main()
