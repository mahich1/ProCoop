from __future__ import annotations

import pandas as pd

from .engine import TradeResult


def trades_to_frame(trades: list[TradeResult]) -> pd.DataFrame:
    rows = []
    for t in trades:
        rows.append({
            "strategy": t.strategy, "direction": t.direction,
            "ts_signal": t.ts_signal, "ts_fill": t.ts_fill,
            "entry": t.entry, "stop": t.stop,
            "ts_exit": t.ts_exit, "exit_price": t.exit_price,
            "r_multiple": t.r_multiple, "outcome": t.outcome,
        })
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame) -> dict:
    filled = df[df["outcome"] != "no_fill"]
    n_signals = len(df)
    n_filled = len(filled)
    if n_filled == 0:
        return {"n_signals": n_signals, "n_filled": 0}

    wins = filled[filled["r_multiple"] > 0]
    losses = filled[filled["r_multiple"] <= 0]
    win_rate = len(wins) / n_filled
    gross_win = wins["r_multiple"].sum()
    gross_loss = -losses["r_multiple"].sum()
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    expectancy_r = filled["r_multiple"].mean()

    equity = filled.sort_values("ts_fill")["r_multiple"].cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    max_dd_r = drawdown.min() if len(drawdown) else 0.0

    return {
        "n_signals": n_signals,
        "n_filled": n_filled,
        "fill_rate": n_filled / n_signals if n_signals else 0.0,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "expectancy_r": expectancy_r,
        "total_r": filled["r_multiple"].sum(),
        "max_drawdown_r": max_dd_r,
        "avg_win_r": wins["r_multiple"].mean() if len(wins) else 0.0,
        "avg_loss_r": losses["r_multiple"].mean() if len(losses) else 0.0,
    }


def summarize_by_strategy(df: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for strat, g in df.groupby("strategy"):
        out[strat] = summarize(g)
    return pd.DataFrame(out).T
