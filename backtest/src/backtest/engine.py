"""Event-driven trade simulator: walks forward from each signal on the
execution timeframe bars, fills at the planned entry (limit-style, price
must trade back into the entry level), then tracks SL/TP.

Conservative fill assumption: when a single bar's range contains both the
stop and a target, the stop is assumed to trigger first.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class TradeResult:
    strategy: str
    direction: int
    ts_signal: pd.Timestamp
    ts_fill: pd.Timestamp | None
    entry: float
    stop: float
    ts_exit: pd.Timestamp | None
    exit_price: float | None
    r_multiple: float
    outcome: str  # "no_fill" | "stop" | "target" | "timeout" | "partial_stop_be"
    meta: dict = field(default_factory=dict)


def _try_fill(bars: pd.DataFrame, start_i: int, max_age: int, entry: float, direction: int):
    n = len(bars)
    for j in range(start_i, min(start_i + max_age, n)):
        bar = bars.iloc[j]
        if bar["low"] <= entry <= bar["high"]:
            return j, bars.index[j]
    return None, None


def simulate_single_target(signals, bars: pd.DataFrame, max_hold_bars: int = 500) -> list[TradeResult]:
    """For S1-style signals: one entry, one stop, one target."""
    results = []
    idx = bars.index
    for sig in signals:
        try:
            start_i = idx.get_indexer([sig.ts_signal])[0] + 1
        except Exception:
            continue
        if start_i <= 0 or start_i >= len(bars):
            continue

        fill_i, fill_ts = _try_fill(bars, start_i, sig.max_entry_age_bars, sig.entry, sig.direction)
        if fill_i is None:
            results.append(TradeResult(sig.strategy, sig.direction, sig.ts_signal, None,
                                        sig.entry, sig.stop, None, None, 0.0, "no_fill", sig.meta))
            continue

        risk = abs(sig.entry - sig.stop)
        outcome, exit_ts, exit_price = "timeout", None, None
        for j in range(fill_i + 1, min(fill_i + 1 + max_hold_bars, len(bars))):
            bar = bars.iloc[j]
            hit_stop = bar["low"] <= sig.stop if sig.direction > 0 else bar["high"] >= sig.stop
            hit_target = bar["high"] >= sig.target if sig.direction > 0 else bar["low"] <= sig.target
            if hit_stop:
                outcome, exit_ts, exit_price = "stop", idx[j], sig.stop
                break
            if hit_target:
                outcome, exit_ts, exit_price = "target", idx[j], sig.target
                break
        if outcome == "timeout":
            j = min(fill_i + max_hold_bars, len(bars) - 1)
            exit_ts, exit_price = idx[j], bars.iloc[j]["close"]

        r = (exit_price - sig.entry) / risk * sig.direction if risk > 0 else 0.0
        results.append(TradeResult(sig.strategy, sig.direction, sig.ts_signal, fill_ts,
                                    sig.entry, sig.stop, exit_ts, exit_price, r, outcome, sig.meta))
    return results


def simulate_two_leg(signals, bars: pd.DataFrame, max_hold_bars: int = 500,
                      leg1_frac: float = 0.5) -> list[TradeResult]:
    """For S2-style signals: entry, stop, TP1 (internal, closes leg1_frac of
    size and moves stop to breakeven), TP2 (external, closes the rest)."""
    results = []
    idx = bars.index
    for sig in signals:
        try:
            start_i = idx.get_indexer([sig.ts_signal])[0] + 1
        except Exception:
            continue
        if start_i <= 0 or start_i >= len(bars):
            continue

        fill_i, fill_ts = _try_fill(bars, start_i, sig.max_entry_age_bars, sig.entry, sig.direction)
        if fill_i is None:
            results.append(TradeResult(sig.strategy, sig.direction, sig.ts_signal, None,
                                        sig.entry, sig.stop, None, None, 0.0, "no_fill", sig.meta))
            continue

        risk = abs(sig.entry - sig.stop)
        if risk <= 0:
            continue

        stop = sig.stop
        leg1_done = False
        r_total = 0.0
        outcome, exit_ts, exit_price = "timeout", None, None

        for j in range(fill_i + 1, min(fill_i + 1 + max_hold_bars, len(bars))):
            bar = bars.iloc[j]
            hit_stop = bar["low"] <= stop if sig.direction > 0 else bar["high"] >= stop
            if hit_stop:
                stop_r = (stop - sig.entry) / risk * sig.direction
                if leg1_done:
                    r_total += (1 - leg1_frac) * stop_r
                    outcome = "partial_stop_be" if stop == sig.entry else "stop"
                else:
                    r_total = stop_r
                    outcome = "stop"
                exit_ts, exit_price = idx[j], stop
                break

            if not leg1_done:
                hit_tp1 = bar["high"] >= sig.target_internal if sig.direction > 0 else bar["low"] <= sig.target_internal
                if hit_tp1:
                    leg1_r = (sig.target_internal - sig.entry) / risk * sig.direction
                    r_total += leg1_frac * leg1_r
                    leg1_done = True
                    stop = sig.entry  # move to breakeven
                    continue

            hit_tp2 = bar["high"] >= sig.target_external if sig.direction > 0 else bar["low"] <= sig.target_external
            if leg1_done and hit_tp2:
                leg2_r = (sig.target_external - sig.entry) / risk * sig.direction
                r_total += (1 - leg1_frac) * leg2_r
                outcome, exit_ts, exit_price = "target", idx[j], sig.target_external
                break

        if outcome == "timeout":
            j = min(fill_i + max_hold_bars, len(bars) - 1)
            last_close = bars.iloc[j]["close"]
            remaining_frac = (1 - leg1_frac) if leg1_done else 1.0
            r_total += remaining_frac * ((last_close - sig.entry) / risk * sig.direction) if not leg1_done else \
                remaining_frac * ((last_close - stop) / risk * sig.direction)
            exit_ts, exit_price = idx[j], last_close

        results.append(TradeResult(sig.strategy, sig.direction, sig.ts_signal, fill_ts,
                                    sig.entry, sig.stop, exit_ts, exit_price, r_total, outcome, sig.meta))
    return results
