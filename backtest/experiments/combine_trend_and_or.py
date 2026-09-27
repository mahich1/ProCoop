"""Portfolio-level check: does combining trend_breakout (D1) with
or_sweep_breakout (M5 intraday) actually diversify risk, or just stack it?

Reconstructs a proper day-by-day mark-to-market R path for the
(multi-day) trend-following trades - allocating a trade's R-multiple
only to its exit date understates real combined drawdown, since an open
trend position has its own daily floating P&L the whole time it's held.
OR trades close same-day, so no reconstruction needed there.

Reuses the trade logs already saved in reports/ - no extra Databento
cost, no re-backtest.

    python3 experiments/combine_trend_and_or.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.core.resample import to_timeframe

REPORTS = Path(__file__).resolve().parents[1] / "reports"
CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"


def _dd_stats(r: pd.Series) -> tuple[float, float]:
    eq = r.cumsum()
    dd = eq - eq.cummax()
    return eq.iloc[-1], dd.min()


def main():
    tf = pd.concat([
        pd.read_csv(REPORTS / "trend_breakout_2020-01-01_2023-01-01.csv"),
        pd.read_csv(REPORTS / "trend_breakout_2023-01-01_2025-01-01.csv"),
    ]).reset_index(drop=True)
    tf["ts_fill"] = pd.to_datetime(tf["ts_fill"], utc=True)
    tf["ts_exit"] = pd.to_datetime(tf["ts_exit"], utc=True)

    d1 = pd.concat([
        to_timeframe(pd.read_parquet(CACHE / "NQ_c_0_2020-01-01_2023-01-01.parquet"), "1D"),
        to_timeframe(pd.read_parquet(CACHE / "NQ_c_0_2023-01-01_2025-01-01.parquet"), "1D"),
    ])
    d1 = d1[~d1.index.duplicated()].sort_index()

    rows = []
    for _, t in tf.iterrows():
        risk = abs(t["entry"] - t["stop"])
        direction = t["direction"]
        span = d1.loc[(d1.index >= t["ts_fill"].tz_convert(d1.index.tz)) &
                       (d1.index <= t["ts_exit"].tz_convert(d1.index.tz))]
        if len(span) == 0:
            continue
        closes = span["close"].copy()
        closes.iloc[-1] = t["exit_price"]
        r_path = (closes - t["entry"]) / risk * direction
        r_path.iloc[0] = 0.0
        daily_pnl = r_path.diff().fillna(r_path.iloc[0])
        for ts, pnl in daily_pnl.items():
            rows.append({"date": pd.Timestamp(ts.date()), "trend_r": pnl})
    trend_mtm = pd.DataFrame(rows).groupby("date")["trend_r"].sum()

    orb = pd.concat([
        pd.read_csv(REPORTS / "or_sweep_breakout_2020-01-01_2023-01-01.csv"),
        pd.read_csv(REPORTS / "or_sweep_breakout_2023-01-01_2025-01-01.csv"),
    ])
    orb["ts_fill"] = pd.to_datetime(orb["ts_fill"], utc=True)
    orb["date"] = orb["ts_fill"].dt.tz_convert("America/New_York").dt.normalize().dt.tz_localize(None)
    or_daily = orb.groupby("date")["r_multiple"].sum().rename("or_r")

    daily = pd.concat([trend_mtm.rename("trend_r"), or_daily], axis=1).fillna(0.0).sort_index()
    daily["combined_r"] = daily["trend_r"] + daily["or_r"]

    for label, col in [("Trend-following (mark-to-market)", "trend_r"),
                        ("OR sweep/breakout", "or_r"), ("Combined", "combined_r")]:
        total, mdd = _dd_stats(daily[col])
        print(f"{label:34s} total_R={total:8.2f}  max_dd_R={mdd:8.2f}  worst_day_R={daily[col].min():6.2f}")

    corr = daily.loc[(daily["trend_r"] != 0) | (daily["or_r"] != 0), ["trend_r", "or_r"]].corr().iloc[0, 1]
    print(f"\nCorrelation (daily R, mark-to-market): {corr:.3f}")
    print(f"\nWorst 5 combined days:\n{daily.nsmallest(5, 'combined_r')}")


if __name__ == "__main__":
    main()
