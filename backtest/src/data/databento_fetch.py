"""Fetch and cache OHLCV-1m bars from Databento (GLBX.MDP3) for continuous
front-month futures, converted to America/New_York local time.

Usage:
    python -m src.data.databento_fetch --symbol NQ.c.0 --start 2023-01-01 --end 2025-01-01

The API key is read from $DATABENTO_API_KEY, or from a file path given via
--key-file (never pass the key as a literal CLI argument — it would end up
in shell history).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"
NY_TZ = "America/New_York"


def _load_key(key_file: str | None) -> str:
    if key_file:
        return Path(key_file).read_text().strip()
    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        raise RuntimeError(
            "No Databento API key found. Set $DATABENTO_API_KEY or pass --key-file."
        )
    return key


def to_ny_time(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert(NY_TZ)
    return df


def fetch_ohlcv_1m(symbol: str, start: str, end: str, key_file: str | None = None,
                    use_cache: bool = True) -> pd.DataFrame:
    """Returns a DataFrame indexed by NY-local tz-aware timestamp with
    columns open, high, low, close, volume.
    """
    import databento as db

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / f"{symbol.replace('.', '_')}_{start}_{end}.parquet"
    if use_cache and cache_path.exists():
        return pd.read_parquet(cache_path)

    key = _load_key(key_file)
    client = db.Historical(key)

    data = client.timeseries.get_range(
        dataset="GLBX.MDP3",
        symbols=[symbol],
        stype_in="continuous",
        schema="ohlcv-1m",
        start=start,
        end=end,
    )
    df = data.to_df()
    df = df.rename(columns={"open": "open", "high": "high", "low": "low",
                             "close": "close", "volume": "volume"})
    df = df[["open", "high", "low", "close", "volume"]]
    df = to_ny_time(df)
    df.to_parquet(cache_path)
    return df


def get_cost_estimate(symbol: str, start: str, end: str, key_file: str | None = None) -> float:
    import databento as db
    key = _load_key(key_file)
    client = db.Historical(key)
    return client.metadata.get_cost(
        dataset="GLBX.MDP3", symbols=[symbol], stype_in="continuous",
        schema="ohlcv-1m", start=start, end=end,
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--key-file", default=None)
    ap.add_argument("--cost-only", action="store_true")
    args = ap.parse_args()

    if args.cost_only:
        cost = get_cost_estimate(args.symbol, args.start, args.end, args.key_file)
        print(f"Estimated cost for {args.symbol} {args.start}->{args.end}: ${cost:.4f}")
    else:
        df = fetch_ohlcv_1m(args.symbol, args.start, args.end, args.key_file)
        print(df.shape)
        print(df.head())
        print(df.tail())
