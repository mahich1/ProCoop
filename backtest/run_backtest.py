"""Entry point: fetch NQ (primary) + ES (SMT correlate) from Databento,
build timeframes, run S1 and S2, print/save a report.

    python run_backtest.py --start 2023-01-01 --end 2025-01-01

The Databento API key is read from $DATABENTO_API_KEY (export it before
running) or from --key-file pointing at a file that contains only the key.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from src.core.resample import to_timeframe
from src.data.databento_fetch import fetch_ohlcv_1m
from src.strategies import s1_raid_smt, s2_turtle_soup
from src.backtest.engine import simulate_single_target, simulate_two_leg
from src.backtest.metrics import trades_to_frame, summarize_by_strategy, summarize

REPORTS_DIR = Path(__file__).resolve().parent / "reports"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--primary", default="NQ.c.0")
    ap.add_argument("--correlate", default="ES.c.0")
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--strategies", default="S1,S2")
    args = ap.parse_args()

    print(f"Fetching {args.primary} 1m {args.start}->{args.end} ...")
    nq_m1 = fetch_ohlcv_1m(args.primary, args.start, args.end, args.key_file)
    print(f"Fetching {args.correlate} 1m {args.start}->{args.end} ...")
    es_m1 = fetch_ohlcv_1m(args.correlate, args.start, args.end, args.key_file)

    nq_m5 = to_timeframe(nq_m1, "5min")
    es_m5 = to_timeframe(es_m1, "5min")
    nq_h1 = to_timeframe(nq_m1, "1h")

    REPORTS_DIR.mkdir(exist_ok=True)
    strategies = args.strategies.split(",")
    all_trades = []

    if "S1" in strategies:
        print("Generating S1 signals ...")
        sigs1 = s1_raid_smt.generate_signals(nq_m5, es_m5)
        print(f"  {len(sigs1)} S1 signals")
        trades1 = simulate_single_target(sigs1, nq_m5)
        all_trades += trades1

    if "S2" in strategies:
        print("Generating S2 signals ...")
        sigs2 = s2_turtle_soup.generate_signals(nq_m5, nq_h1)
        print(f"  {len(sigs2)} S2 signals")
        trades2 = simulate_two_leg(sigs2, nq_m5)
        all_trades += trades2

    df = trades_to_frame(all_trades)
    df.to_csv(REPORTS_DIR / f"trades_{args.start}_{args.end}.csv", index=False)

    print("\n=== Summary by strategy ===")
    print(summarize_by_strategy(df))
    print("\n=== Overall ===")
    print(summarize(df))


if __name__ == "__main__":
    main()
