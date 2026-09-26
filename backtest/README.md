# Backtest S1 / S2 — NASDAQ (NQ futures)

Backtest engine for two discretionary ICT-style setups, built off the
concepts in `ICT_V2_7_0_MULTI_ROUTES_INDICATOR.pine` (sweep of liquidity,
SMT, displacement/MSS, FVG, Premium/Discount) but implemented as a
standalone Python pipeline rather than a port of the Pine script.

- **S1 — Raid HTF + SMT + déplacement** : accumulation -> sweep de
  PDH/PDL ou Asian High/Low en killzone -> SMT sur l'actif corrélé au
  moment du raid -> displacement + MSS -> FVG -> entrée en CE (50%) ->
  cible : liquidité opposée.
- **S2 — Turtle Soup dans un PD Array HTF** : prix entre dans une zone
  H1/H4 (OB/FVG) -> sweep d'un high/low local à l'intérieur de la zone ->
  displacement + MSS M5 -> entrée sur FVG M5 en CE -> cibles : liquidité
  interne puis externe.

## Status

The detection + simulation pipeline is implemented, validated against
hand-crafted synthetic data (`tests/test_synthetic.py`), and has now been
run once against real data (NQ.c.0 / ES.c.0, GLBX.MDP3, 1-minute bars,
2023-01-01 → 2025-01-01, cost ≈ $5.13 on Databento).

Run the sanity tests any time with:

```bash
python3 tests/test_synthetic.py
```

## Results (2023-01-01 → 2025-01-01, NQ.c.0)

Trade log: `reports/trades_2023-01-01_2025-01-01.csv`. All figures are in
R (risk multiples), independent of position sizing.

| | signals | filled | fill rate | win rate | profit factor | expectancy | total R | max DD (R) |
|---|---|---|---|---|---|---|---|---|
| **S1** | 6 | 2 | 33% | 0% | 0.00 | -1.00R | -2.0R | -1.0R |
| **S2** | 56 | 32 | 57% | 34% | 0.39 | -0.41R | -13.3R | -15.5R |
| **Overall** | 62 | 34 | 55% | 32% | 0.36 | -0.45R | -15.3R | -17.5R |

**As implemented here, over this 2-year window, neither strategy is
profitable.** Read this with two big caveats:

1. **S1's sample is tiny (6 signals in 2 years, only 2 filled).** That's
   not enough to draw any conclusion either way — the raid+SMT+killzone
   conjunction is just very rare with these parameters. It needs either a
   much longer lookback or loosened filters (killzone window, SMT
   sync tolerance, displacement thresholds) before the win/loss numbers
   mean anything.
2. **This is a direct implementation of the plain-English S1/S2
   descriptions, not a port of `reference/ICT_V2_7_0_MULTI_ROUTES_INDICATOR.pine`.**
   The indicator itself carries additional filters (H1 bias gate, H4 veto,
   Premium/Discount gate, minimum-RR gate, fresh-liquidity requirement)
   that its own changelog says were tested and *disabled by default*
   because they lost money on the author's own NAS100 backtests — this
   run's poor S2 profit factor is broadly consistent with that finding,
   but the two are not directly comparable (different entry mechanics,
   different sample window, no H1/H4 gating here).

Likely next steps if you want to keep pushing on this: loosen S1's
filters to get a usable sample size, try the indicator's optional gates on
S2 to see if they help here too, and/or widen the date range once you've
seen these numbers hold (or not) out of sample.

## Parameter sensitivity (`experiments/param_sweep.py`)

A first pass at loosening/tightening the default filters, still on the
same 2023-2025 NQ window (reuses the cached parquet in `data/cache/`, no
extra Databento cost):

| S1 variant | signals | filled | win rate | PF | expectancy | total R |
|---|---|---|---|---|---|---|
| baseline | 6 | 2 | 0% | 0.00 | -1.00R | -2.0R |
| **wide_kz_sync2** (killzone merged into one continuous 02:00-11:00 NY window, SMT sync tolerance 1→2 bars, MSS lookahead 12→24, POI validity 36→60) | **16** | **11** | **36%** | **3.52** | **+1.61R** | **+17.7R** |
| loose_disp (looser displacement thresholds on top of the above) | 14 | 6 | 0% | 0.00 | -1.00R | -6.0R |

| S2 variant | signals | filled | win rate | PF | expectancy | total R |
|---|---|---|---|---|---|---|
| baseline | 56 | 32 | 34% | 0.39 | -0.41R | -13.3R |
| **wider_buffer** (stop buffer 0.1→0.25 ATR, entry fill window 12→24 bars) | 56 | 40 | 38% | 0.59 | -0.26R | -10.5R |
| wider_buffer_looser_disp | 40 | 30 | 30% | 0.45 | -0.46R | -13.7R |
| tighter_zone_age (HTF zone max age 80→40 H1 bars, on top of wider_buffer) | 54 | 40 | 38% | 0.59 | -0.26R | -10.5R |

Two clear directional findings:

- **The killzone/SMT tolerance, not the displacement strictness, was
  starving S1 of sample size.** Merging London+NY-AM into one continuous
  window and giving SMT pivots ±2 bars of sync tolerance took S1 from 2
  filled trades to 11, and flipped it from a single -2R loss to +17.7R
  (PF 3.52). **Loosening displacement thresholds made both strategies
  worse**, not better — the follow-through strength is doing real work,
  it isn't just a sample-size tax.
- **Widening S2's stop and giving entries more time to fill helps but
  doesn't flip it profitable** (PF 0.39→0.59, expectancy -0.41R→-0.26R).
  HTF zone age wasn't a binding constraint (80 vs 40 H1 bars gave
  near-identical results).

**Take the S1 `wide_kz_sync2` result with real caution**: it's the best
of 3 variants tried on the *same* 2-year window used to pick it, and 11
filled trades is still a small sample — this is exactly the setup for
overfitting to one period. Before trusting it, it needs to hold up on a
different date range and/or a different but related instrument (e.g.
ES/MES) without re-tuning.

## Out-of-sample check: it didn't hold up

Ran `wide_kz_sync2` unchanged (no re-tuning) on NQ/ES 2020-01-01 →
2023-01-01 — three years, zero overlap with the 2023-2025 window it was
picked on:

| window | signals | filled | win rate | PF | expectancy | total R | avg win R |
|---|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 16 | 11 | 36% | 3.52 | +1.61R | +17.7R | 6.17R |
| 2020-2023 (out-of-sample) | 18 | 11 | 36% | 0.99 | -0.01R | -0.08R | 1.73R |

**The overfitting warning played out exactly as flagged.** Win rate held
almost identically across both windows (36%), but the in-sample profit
factor was being carried by a handful of unusually large winners
(avg win 6.17R) that simply weren't there in 2020-2023 (avg win 1.73R) —
out of sample this setup is a coin flip at breakeven, not the +17.7R
edge it looked like. That's consistent with 2023-2025 NQ having had a
few outsized directional moves the setup happened to catch, rather than
S1 (even with the loosened killzone/SMT filters) having a genuine,
repeatable edge on this instrument.

**Bottom line so far: neither S1 nor S2, in any variant tried, has shown
a robust edge on NQ.** The honest next steps are either (a) treat this as
a negative result and look for what's structurally different about the
plain-English S1/S2 recipes vs. the reference indicator's actual (more
heavily gated) entry logic, or (b) test on a different but related market
(ES/MES) to see if the pattern is instrument-specific.

## Layout

```
backtest/
  src/
    data/databento_fetch.py   # Databento GLBX.MDP3 OHLCV-1m fetch + parquet cache
    core/
      sessions.py             # killzones, PDH/PDL, Asia/London/OR session H/L
      structure.py            # pivots, BOS/MSS, FVG, order block, ATR
      smt.py                  # cross-instrument SMT divergence
      resample.py             # M1 -> M5/H1/H4/D1
    strategies/
      s1_raid_smt.py
      s2_turtle_soup.py
    backtest/
      engine.py                # trade simulator (fill/SL/TP)
      metrics.py                # win rate, profit factor, expectancy, drawdown
  run_backtest.py
  tests/test_synthetic.py
```

## Running the real backtest (once Databento access is unblocked)

```bash
export DATABENTO_API_KEY=db-xxxxxxxx   # or pass --key-file
python3 run_backtest.py --start 2023-01-01 --end 2025-01-01 \
    --primary NQ.c.0 --correlate ES.c.0 --strategies S1,S2
```

This fetches 1-minute continuous-front-month bars for NQ (primary) and ES
(SMT correlate) from `GLBX.MDP3`, resamples to M5/H1, runs both strategies,
and writes `reports/trades_<start>_<end>.csv` plus a summary printed to
stdout (win rate, profit factor, expectancy in R, max drawdown, per
strategy and overall).

Before pulling a large date range, check the cost first:

```bash
python3 -m src.data.databento_fetch --symbol NQ.c.0 --start 2023-01-01 --end 2025-01-01 --cost-only
```

## Design notes / simplifications vs. the Pine indicator

- Entry/exit timeframe is M5 for both strategies (the indicator also
  supports M1 entries for its OR+FVG route; swap `nq_m5` for a resampled
  M1 frame in `run_backtest.py` if you want that granularity for S2).
- Killzones default to the standard London (02:00-05:00 NY) and NY AM
  (08:30-11:00 NY) windows.
- S1 targets the opposite daily/session liquidity with a configurable
  minimum RR fallback; S2 uses a two-leg exit (internal target at 50% size,
  stop moved to breakeven, external target for the rest).
- Fill simulation is conservative: when a single bar's range contains both
  the stop and a target, the stop is assumed to trigger first.
- SMT divergence matches confirmed swing pivots between NQ and ES within a
  small time-sync tolerance, mirroring the indicator's `smtSyncBars`.
