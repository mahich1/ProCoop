"""In-sample sweep + out-of-sample validation for S3 (VWAP deviation fade).
Reproduces the numbers in README.md's "S3" section. Reuses cached parquet
in data/cache/ - no extra Databento cost.

    python3 experiments/s3_sweep.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from src.backtest.engine import simulate_two_leg
from src.backtest.metrics import summarize, trades_to_frame
from src.core.resample import to_timeframe
from src.strategies import s3_vwap_fade

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 20)

CACHE = Path(__file__).resolve().parents[1] / "data" / "cache"

IN_SAMPLE = ("2023-01-01", "2025-01-01")
OUT_OF_SAMPLE = ("2020-01-01", "2023-01-01")


def _load_m5(start: str, end: str, symbol: str = "NQ") -> pd.DataFrame:
    m1 = pd.read_parquet(CACHE / f"{symbol}_c_0_{start}_{end}.parquet")
    return to_timeframe(m1, "5min")


def _run(m5: pd.DataFrame, **kwargs) -> dict:
    sigs = s3_vwap_fade.generate_signals(m5, **kwargs)
    trades = simulate_two_leg(sigs, m5)
    df = trades_to_frame(trades)
    return summarize(df) if len(df) else {"n_signals": 0}


def main():
    m5_in = _load_m5(*IN_SAMPLE)
    m5_oos = _load_m5(*OUT_OF_SAMPLE)

    print("=== In-sample sweep (2023-2025) ===")
    variants = {
        "k1.5_default": dict(k_entry=1.5),
        "k1.0": dict(k_entry=1.0),
        "k2.0": dict(k_entry=2.0),
        "k1.5_2trades": dict(k_entry=1.5, max_trades_per_day=2),
    }
    rows = [dict(_run(m5_in, **kw), variant=name) for name, kw in variants.items()]
    print(pd.DataFrame(rows).set_index("variant").to_string())

    print("\n=== k1.5_default: in-sample vs out-of-sample (unchanged) ===")
    rows = [
        dict(_run(m5_in, k_entry=1.5), window="2023-2025 (in-sample)"),
        dict(_run(m5_oos, k_entry=1.5), window="2020-2023 (out-of-sample)"),
    ]
    print(pd.DataFrame(rows).set_index("window").to_string())

    print("\n=== Midday-chop hypothesis (11:30-14:00), both windows ===")
    kwargs = dict(k_entry=1.5, session_start="11:30", session_end="14:00")
    rows = [
        dict(_run(m5_in, **kwargs), window="2023-2025"),
        dict(_run(m5_oos, **kwargs), window="2020-2023"),
    ]
    print(pd.DataFrame(rows).set_index("window").to_string())


if __name__ == "__main__":
    main()
