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

The detection + simulation pipeline is implemented and validated against
hand-crafted synthetic data (`tests/test_synthetic.py`) — it correctly
fires signals with the expected direction/levels and the trade engine
correctly fills/exits them. **It has not yet been run against real market
data**: this sandbox's network policy blocks `hist.databento.com`
(confirmed via `curl $HTTPS_PROXY/__agentproxy/status`), so pulling NQ/ES
data from Databento is pending an egress allowlist update.

Run the sanity tests any time with:

```bash
python3 tests/test_synthetic.py
```

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
