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

## S3 — VWAP deviation fade (built for higher win rate / challenge fit)

S1/S2 are low-frequency, asymmetric-RR setups - exactly the wrong shape
for a prop-firm challenge, where a handful of trades either blow the
daily-loss limit or barely dent the profit target. `s3_vwap_fade.py` is a
different kind of strategy on purpose: mechanical, single-instrument,
one clean idea (fade an overstretched move back toward session VWAP once
a reversal candle confirms), aimed at a **higher win rate and much larger
trade count** so the statistics are actually meaningful.

Rules: skip the first ~25 min of the session; when `|close - VWAP| / ATR
≥ k_entry` **and** the current candle already shows a pullback (red for a
short fade, green for a long fade), enter at the next bar's open; stop
beyond the signal candle's extreme + 0.3 ATR; take half size at the
midpoint back to VWAP (stop then to breakeven), the rest at VWAP itself
(snapshotted at signal time - a documented simplification, VWAP isn't
re-targeted bar by bar).

**In-sample (2023-2025, tuning k_entry and trades/day):**

| variant | signals | win rate | PF | expectancy | total R | max DD |
|---|---|---|---|---|---|---|
| k_entry=1.5, 1 trade/day | 505 | 48% | 1.16 | +0.084R | +42.3R | -14.2R |
| k_entry=1.0 | 508 | 51% | 1.00 | +0.002R | +1.2R | -25.8R |
| k_entry=2.0 | 486 | 43% | 1.14 | +0.079R | +38.2R | -18.3R |
| k_entry=1.5, 2 trades/day | 1004 | 45% | 1.05 | +0.026R | +26.5R | -33.1R |

k_entry=1.5 at one trade/day looked the most promising (505 trades is a
real sample, PF 1.16). **Ran it unchanged on 2020-2023:**

| window | signals | win rate | PF | expectancy | total R | max DD |
|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 505 | 48% | 1.16 | +0.084R | +42.3R | -14.2R |
| 2020-2023 (out-of-sample) | 758 | 42% | 0.95 | -0.031R | **-23.3R** | -37.4R |

Same story as S1: the edge didn't survive. One thing *did* hold up across
both windows - the win/loss size ratio (avg win ≈1.2-1.5R vs a full -1R
loss stayed consistent) - but the win rate itself swung from profitable
to unprofitable between periods, which means the entry signal isn't
reliably picking a favorable side, only the exit structure is stable.

**One more hypothesis, tested honestly (not fit to either window):**
restrict entries to 11:30-14:00 NY, the classic "lunch chop" window,
on the reasoning that mean reversion should be more reliable away from
the trend-prone open. Tested on both windows at once, no cherry-picking:

| window | signals | win rate | PF | expectancy | total R |
|---|---|---|---|---|---|
| 2023-2025 | 473 | 40% | 0.99 | -0.005R | -2.4R |
| 2020-2023 | 713 | 39% | 0.94 | -0.038R | -27.2R |
| **combined 2020-2025** | **1186** | **40%** | **0.96** | **-0.025R** | **-29.6R** |

It made things worse, not better - the full-session version was
better than the midday-only one in both windows.

## Two more hypotheses (regime filters), and a look-ahead bug caught along the way

Two follow-ups, both built from OHLCV alone (no order flow/DOM available):
an H1 EMA trend filter on S3 (only fade *with* the prevailing H1 trend -
pullback entries instead of pure counter-trend fading), and H1 EMA
bias / H4 EMA veto gates on S1 (a mechanical proxy for the confluences a
discretionary trader would want - the same two gates the reference
indicator has, disabled by default there).

**First pass looked spectacular for S3**: win rate 48%→69%, PF 1.16→2.73,
and it *held up* on 2020-2023 unchanged (65% win rate, PF 2.41,
+154R). Before believing a result that good, the alignment code
(`core/regime.py: align_to`) got a hard look - and it had a real
look-ahead bug. `to_timeframe` labels an HTF bar by its *open* time, so
the H1 bar labeled 10:00 only finishes closing at 11:00; the alignment
helper was forward-filling that bar's EMA/bias value starting at 10:00
itself, letting the filter see up to an hour of future price action
before it happened. Fixed by lagging the HTF series one bar before
aligning (`align_to(..., lag=1)`, now the default).

**Rerun with the fix, the S3 result evaporated:**

| S3 variant | signals | win rate | PF | expectancy | total R |
|---|---|---|---|---|---|
| no filter (baseline) | 505 | 48% | 1.16 | +0.084R | +42.3R |
| H1 trend filter (buggy, leaked ~1h of future price) | 209 | 69% | 2.73 | +0.531R | +111.0R |
| **H1 trend filter (fixed)** | **278** | **48.6%** | **0.98** | **-0.008R** | **-2.4R** |

Once the leak was closed, the trend filter is statistically the same as
no filter at all (or marginally worse) - it added nothing real. **The
entire earlier "69% win rate" result was the bug**, not a discovery.

The S1 gates (H1 bias, H4 veto, both) weren't materially affected by
this particular bug (a look-ahead leak tends to inflate results, and
these were already collapsing to a handful of all-losing trades before
the fix too) - re-confirmed unprofitable after the fix, consistent with
the reference indicator's own documented finding that these same gates
lose money when tested.

**Verdict: none of the three strategies tested in this repo (S1, S2, S3,
in every variant tried) has a validated, out-of-sample edge on NQ over
2020-2025.** That's a real, useful answer, not a non-answer: at M5/H1 on
plain OHLCV bars, simple ICT-style raid setups and simple VWAP
mean-reversion both come out statistically indistinguishable from zero
edge once tested honestly, on a market (NQ futures) that's about as
liquid and heavily-traded-by-similar-strategies as it gets. That's
consistent with what quantitative research generally finds about
retail-style single-instrument price patterns on major index futures.

**What this means for using any of this on a funded-account challenge:
don't.** Not as pure mechanical systems, not yet. Passing a challenge is
less about win rate and more about surviving the daily/max-loss limits
long enough for a genuine (even modest) edge to play out - and none of
these three has demonstrated one, including after adding the two most
obvious OHLCV-only regime filters (H1 trend alignment, H1 bias/H4 veto).
Putting real risk behind an unvalidated system is choosing to gamble with
the challenge fee and, if funded, real drawdown limits.

Two of the three follow-up options from the previous round are now
closed off (OHLCV-derived regime filters: tried, no edge; discretionary-
style H1/H4 confluence gates: tried, actively hurts). What's left,
honestly: (a) an information source genuinely outside what's in this
repo - real order flow/DOM, options positioning, a cross-asset macro
regime signal, not just another EMA on the same OHLCV bars; (b) the same
three designs on a different, possibly less arbitraged instrument or
timeframe; or (c) accept that S1's raid logic may only work as a
discretionary confirmation layer under a human's live judgment, which is
a fundamentally different (and much harder to backtest) thing than a
mechanical trigger, and isn't something this repo can validate for you.

## Testing the actual indicator (`ICT_V2_7_8_CLEAR_PLAN.pine`), not an approximation

Everything above tested plain-English recipes I wrote from the setup
descriptions. The user then supplied the real Pine indicator and asked
for the actual strategy to be tested and its weak points identified.
`src/strategies/core_reversal.py` is a direct port of the file's default
"moteur strict" - Sweep -> CISD -> POI (IFVG or displacement OB) -> 50%
retracement -> GO - read from the source line by line, not reconstructed
from the comments. Concretely more faithful than S1 in three ways that
matter:

- **Sweep consumption is per price level, not per day** (`f_firstCrossAvailable`,
  the V2.7.7 change): once a level is crossed by a close, it's "spent"
  until the price itself changes (new PDH, new session, ...), and the
  sweep bar must be the *first* bar to cross it (`low[1] >= level`).
- **CISD is a persistent level, not "close beyond a swing"**: it's the
  OPEN of the first candle of the run that just ended, tested on every
  closed bar until it fires or ages out (`f_cisdPersistent`) - not what
  S1 approximated.
- **POI is the current-timeframe IFVG or the CISD-candle's own
  displacement, not an HTF zone** - matching the "IFVG ou OB" default
  mode exactly, including the case where the OB *is* the CISD-confirming
  candle itself.

The confluence gates the script ships (H1 bias, H4 veto, Premium/Discount,
minimum-RR) are left OFF, matching the shipped defaults - the script's
own comments say they were tested and disabled because they lost money;
they only produce a grade (A+/A/C) and don't block a GO in this
configuration. Not ported: the HTF POI module (only an optional,
off-by-default confluence), the additive SMT/CISD/MSS route, the
H1->M5 Continuation engine, and the OR+FVG M1 route - four separate
engines in the file that don't feed the core one tested here.

### Results, same discipline (design on 2023-2025, validate unchanged on 2020-2023)

| window | signals | win rate | PF | expectancy | total R | max DD |
|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 276 | 43.1% | 1.19 | +0.107R | +29.4R | -21.8R |
| 2020-2023 (out-of-sample) | 378 | 40.5% | 0.97 | -0.015R | -5.7R | -38.1R |

Trade logs: `reports/core_reversal_2023-01-01_2025-01-01.csv` and
`reports/core_reversal_2020-01-01_2023-01-01.csv`.

This is a meaningfully different picture from S1's approximation, which
went from a spectacular-looking PF 3.52 to a near-total collapse. Here,
with a much larger and more faithful sample (276-378 trades per window,
vs. 6-16 before), the profit factor sits right around 1.0 in *both*
windows (0.97 and 1.19) - not a dramatic overfit-and-collapse pattern,
but not a validated edge either. Combined across 5 years (654 trades),
net expectancy is essentially zero (+23.7R total, ~0.036R/trade - well
within noise for this sample size). Outcome breakdown rules out one
obvious failure mode: "timeout" (target too far to realistically reach)
is rare (1-3 trades out of hundreds per window), so the target-selection
logic isn't secretly padding the numbers with unreachable targets.

### Where the actual weak points are (code-level, from reading the source)

1. **The core sequence has no edge once the gates that would otherwise
   filter it are off - and they're off by design.** The script's own
   changelog says `requirePdGate`, `requireRrGate`, `requireBiasGate` and
   the H4 veto were measured to *lose* money on the author's own NAS100
   backtests and were disabled for that reason. That's consistent with
   what both our tests found (this repo's regime filters and the
   script's own gates) - but it means the *shipped, default* strategy
   runs on the raw sequence alone, which nets out to roughly zero edge.
   There's no configuration currently in the file, on by default or
   available as a toggle, that has demonstrated a real edge.
2. **The checklist/grading system (A+/A/B/C) doesn't gate anything by
   default** (`useOptionalGate = false`) - every setup that reaches the
   50% retracement fires a GO regardless of grade. If the tool is meant
   to be used as a discretionary checklist (its own title: "CHECKLIST
   PRÉ-TRADE") where a trader only acts on A+/A signals, that's a
   materially different, untested strategy from the one backtested here
   - and grade alone (per the indicator's own V2.5.11 comment, measured
     on NAS100 2005-2020) did not separate winners from losers either
     (A+ -0.034R, A -0.093R, C -0.080R - the ranking is right, all three
     lose).
3. **Two trades/day, stopped at -1R each, is a rough fit for a
   daily-loss-limited challenge account.** With a ~40% single-trade win
   rate and independent-ish trades, hitting the daily cap of 2 signals
   both losing (-2R on the day) isn't a tail event - it's roughly a
   1-in-3 day, on a strategy that isn't even net positive out of sample.
4. **The long history of correctifs (C1 through C14) reads as a
   strategy that's hard to specify precisely, which is itself a signal.**
   Several of these are genuine, well-reasoned bug fixes worth keeping
   regardless of the edge question (see below) - but the sheer number of
   iterations needed to close edge cases (a level re-crossed after
   reclaim, a stop that could sit inside an already-traded zone, a CISD
   lost because it fired on its own arming candle, mutating arrays on
   unconfirmed ticks) suggests the setup's boundaries are genuinely
   fuzzy, which tends to go along with a thin or absent edge rather than
   a robust one.
5. **Minor infidelity in this port, not a bug in the source:** when
   multiple sweep sources qualify on the same bar, this port's tie-break
   order (PDL, Asia, London, OR, M15) doesn't exactly match the script's
   (PDL, London, Asia, OR, M15) — cosmetic (only affects which name gets
   logged when two sources are hit simultaneously, not the trade itself)
   but worth knowing if you diff behavior candle by candle.

### What to keep

The engineering, not the edge. Specifically:
- **The correctifs are real fixes and should stay**: per-price-level
  sweep consumption (V2.7.7), persistent CISD tested every bar instead
  of only on the reversal candle (C1/V2.5.4), cancel-on-stop-first
  (C3), reclaim invalidation with a 3-close tolerance instead of 1
  (C14), confirmed-bar-only array mutation (C2), and the trailing
  sweep-extreme option (C9) all fix real, well-documented edge cases -
  they make the tool more correct as a *checklist*, independent of
  whether the mechanical version has an edge.
- **The sequence logic (sweep -> CISD -> POI -> retracement) is a
  reasonable, well-structured definition of the setup** - the issue
  isn't that it's incoherent, it's that, backtested honestly and
  mechanically over 5 years of NQ, it doesn't clear the bar of a real
  edge without the gates that the author already found don't help
  either.
- **Don't keep**: the assumption that disabling the confluence gates is
  a free upgrade. It avoids the specific ways those gates were measured
  to lose money, but the alternative it leaves you with is not
  validated to win money - it's closer to noise, per the numbers above.
- **If you want to keep using this indicator**, the honest path is
  either (a) treat it strictly as a discretionary checklist and apply
  your own judgment on top of the A+/A/B/C grade rather than trading
  every GO mechanically (untested here, and hard to backtest by nature),
  or (b) accept that, as a mechanical system, it needs a genuinely new
  edge source layered on before real money - the same conclusion this
  repo already reached with S1/S2/S3.

## ICT "2022 model" (`src/strategies/ict_2022_model.py`)

User-supplied screenshot of the classic ICT 2022 model: liquidity taken
-> market structure shift -> FVG left behind -> retrace into it -> DOL
(opposite liquidity), traded only in the AM (08:30-11:00 NY) and PM
(13:30-16:00 NY) index-futures sessions - no SMT requirement, unlike S1.
Same skeleton as S1 minus the SMT check, reusing already-validated code.

| window | signals | filled | win rate | PF | expectancy | total R | max consec. losses |
|---|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 136 | 81 | 13.6% | 1.05 | +0.043R | +3.5R | 19 |
| 2020-2023 (out-of-sample) | 181 | 105 | 11.4% | 0.65 | -0.31R | **-32.4R** | **37** |

**Worse than every other strategy tested in this repo, and a textbook
overfitting trap.** The in-sample result looked marginally positive, but
81 filled trades included exactly one +49.5R short that carries the
entire total - remove it and in-sample is deeply negative too. Out of
sample, that kind of outlier didn't repeat (best win there was +8.1R),
leaving only the low win rate exposed: PF 0.65, -32.4R over 105 trades,
and a 37-trade losing streak on the short side alone.

By direction (out-of-sample, as asked): **short** PF 0.37, -31.5R over 54
trades - clearly losing; **long** PF 0.98, -0.85R over 51 trades - flat,
not an edge either. Neither side works. Trade logs:
`reports/ict2022_2023-01-01_2025-01-01.csv` and
`reports/ict2022_2020-01-01_2023-01-01.csv`.

The low win rate (10-16%) combined with the fixed-liquidity target
(PDH/PDL, often far from entry) makes this a high-variance, low-hit-rate
system that needs its rare big winners to show up on schedule - and nothing
in a 5-year sample suggests they do reliably. Consistent with everything
else found here: the "sweep -> displacement -> FVG -> retrace" skeleton
alone, in any of the several exact variants tried across S1/CoreReversal/
this model, has not produced a validated edge on NQ.

## Top-down HTF PD array model (`src/strategies/topdown_pd_array.py`)

Another user-supplied 5-step screenshot: D1 + H4 + H1 structure must
agree, then find the HTF PD array (implemented as an unmitigated H4 FVG,
reusing S2's zone bookkeeping), a London-session liquidity sweep testing
that zone, a 5-minute BOS + FVG in the HTF bias direction, entry on the
M5 FVG with a fixed 1:3R target and a 1R partial (stop to breakeven).

| window | signals | filled | win rate | PF | expectancy | total R |
|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 7 | 5 | 60% | 0.75 | -0.10R | -0.5R |
| 2020-2023 (out-of-sample) | 17 | 11 | 36% | 0.50 | -0.32R | -3.5R |

**The honest headline here is the sample size, not the sign of the
result: 16 filled trades combined over 5 years is nowhere near enough to
draw a statistical conclusion either way.** Requiring D1+H4+H1 alignment
*and* a specific session *and* a zone-entry-then-sweep sequence is
extremely restrictive - this is the same problem S1's original
killzone-only version had (6 signals/2yr) before it was loosened, except
here loosening would mean weakening the very top-down alignment the
model is built around, which isn't a change to make lightly. For what
it's worth, both windows independently came out with PF < 1 (0.75 and
0.50) rather than one strong and one weak - a small lean toward "no edge"
rather than "not enough data to tell," but with n=16 that's a lean, not
a verdict. Trade logs: `reports/topdown_2023-01-01_2025-01-01.csv` and
`reports/topdown_2020-01-01_2023-01-01.csv`.

If you want a real answer for this one specifically, it needs either a
much longer backtest window (10+ years, to get a usable sample without
touching the alignment requirement) or accepting that a setup this
selective is inherently something to grade case-by-case rather than
backtest at scale.

## Trend-following breakout (`src/strategies/trend_breakout.py`) - the first result that actually held up

Every setup above is an ICT-style intraday reversal pattern, and none of
them survived out-of-sample testing. This is deliberately the opposite
shape: a classic Donchian-channel breakout, Turtle System 1 style, on
**daily** bars - long on a 20-day high breakout, short on a 20-day low
breakout, initial stop at 2xATR(20), exit when the close crosses the
*opposite* 10-day channel (no fixed profit target - ride the trend until
it turns), one position at a time, no pyramiding.

| window | trades | win rate | PF | expectancy | total R | max DD | max consec. losses |
|---|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 17 | 53% | 2.49 | +0.55R | +9.4R | -3.16R | 4 |
| 2020-2023 (out-of-sample) | 29 | 38% | 1.21 | +0.09R | +2.7R | -4.12R | 4 |
| **combined 2020-2025** | **46** | **43%** | **1.64** | **+0.26R** | **+12.1R** | **-4.12R** | **4** |

**This is the first strategy tested in this repo where the profit factor
stayed above 1.0 in both independent windows.** It cooled off out of
sample (2.49 -> 1.21, as basically every backtest does), but it didn't
flip to unprofitable or collapse into noise the way S1, S3's regime
filter, CoreReversal, and both ICT models all did. The drawdown profile
is also structurally healthier: max 4 consecutive losses (vs. 11-37 for
the ICT systems) and a max drawdown (-4.1R) that's a fraction of the
total return (+12.1R) - roughly a 3:1 recovery ratio, something none of
the other strategies here came close to.

**Caveats, in order of importance:**
- **46 trades over 5 years (~9/year) is still a modest sample** - real,
  but nowhere near the hundreds of trades that would make this a
  statistically airtight case. Treat it as a promising, not proven,
  result.
- **Only one parameter set was tried** (20-day entry / 10-day exit /
  2xATR stop - the standard Turtle System 1 numbers), deliberately,
  to avoid the sweep-until-something-looks-good trap that burned S1 and
  S3's regime filter earlier. It has not been checked for sensitivity to
  nearby parameters (e.g. 15/8 or 25/12) - that's a reasonable next
  step, but every additional variant tried is another chance to
  overfit, so it should be done sparingly and judged the same way
  (design on one window, validate unchanged on the other).
- **No fixed profit target means no clean R-multiple ceiling** - a
  handful of trades that ride a strong trend for a long time are doing
  a lot of the total-return work (same shape as any trend system), so
  the total-R figure is more sensitive to a few big trades than the
  ICT setups' fixed-target trades were.
- Structurally low frequency (~9 trades/year) is a good fit for a
  daily/total-loss-limited challenge account (see the earlier risk
  management discussion) - fewer trades means fewer chances to stack
  losses on the same day, though it also means it takes longer to reach
  a profit target.

Trade logs: `reports/trend_breakout_2023-01-01_2025-01-01.csv` and
`reports/trend_breakout_2020-01-01_2023-01-01.csv`. Next reasonable
steps if you want to build confidence further: check parameter
sensitivity (once, on one window, then re-validate unchanged), test the
same rules on ES/other correlated futures for cross-instrument
consistency, and extend the backtest further back if more history is
affordable, all before sizing any real risk behind it.

## NY Opening Range sweep/breakout (`src/strategies/or_sweep_breakout.py`)

User's model: build the 09:30-09:45 NY Opening Range, then from 09:45 to
16:00 take the FIRST move on either side (ORH or ORL) - traded as a
breakout continuation if the bar closes beyond the level, or a sweep
fade if it wicks beyond and closes back inside. Max one trade/day, fixed
2R target, entry at the next bar's open. (Building this also surfaced
and fixed a real bug: `session_high_low`'s index used raw `datetime.date`
objects instead of `Timestamp`, so every lookup against it silently
failed - see the commit for which earlier strategies' Asia/London/OR
sweep sources were quietly inert as a result; PDH/PDL/M15 still worked,
so those strategies' no-edge conclusions stand.)

| window | signals | win rate | PF | expectancy | total R | max DD | max consec. losses |
|---|---|---|---|---|---|---|---|
| 2023-2025 (in-sample) | 508 | 37.8% | 1.15 | +0.09R | +46.6R | -25.2R | 14 |
| 2020-2023 (out-of-sample) | 763 | 38.5% | 1.19 | +0.12R | +90.3R | -27.1R | 10 |
| **combined 2020-2025** | **1271** | **38.2%** | **1.18** | **+0.11R** | **+136.8R** | **-27.1R** | 14 |

**PF didn't just survive out-of-sample, it held essentially flat (1.15
-> 1.19)** - and this time on a genuinely large sample (1271 trades
combined, roughly one a day), which is a different level of statistical
weight than the 46-trade trend-following result. Broken down by type:

| kind | in-sample PF | out-of-sample PF |
|---|---|---|
| breakout (close beyond ORH/ORL) | 1.23 | 1.25 |
| sweep (wick beyond, close back inside) | 1.07 | 1.13 |

The breakout half is the stronger, more consistent contributor in both
windows; the sweep half is thinner but still > 1.0 both times, not the
"looks fine then collapses" pattern every ICT setup showed. Recovery
ratio (total return / max drawdown) is roughly 5:1, better than the
trend-following system's ~3:1.

**Caveats:** this fires close to once a day, every day - fine for
statistical weight, but it means daily-loss-limit exposure is back on
the table the way it wasn't for the once-a-week trend system (see the
earlier risk-management discussion: at this trade frequency, sizing
still needs to respect the daily cap, not just the total one). The 14
max-consecutive-losses (in-sample) is real and will happen again. Only
one exact rule set was tested (2R target, 2-tick-free stop placement at
the opposite OR side / sweep wick) - deliberately, same reasoning as the
trend system: no parameter search behind this result.

Trade logs: `reports/or_sweep_breakout_2023-01-01_2025-01-01.csv` and
`reports/or_sweep_breakout_2020-01-01_2023-01-01.csv`.

## Combining trend-following + OR sweep/breakout (`experiments/combine_trend_and_or.py`)

Both surviving strategies (trend_breakout, or_sweep_breakout), run as one
portfolio: does diversification actually reduce risk, or just stack it?
Reuses the saved trade logs, no re-backtest. Trend-following trades can
stay open for days, so a day-by-day mark-to-market R path is
reconstructed for them (D1 close vs. entry, divided by risk) rather than
dumping the whole trade's R onto its exit date - that naive approach
understates real combined drawdown, since an open position has its own
daily floating P&L the whole time it's held. OR trades close same-day,
so no reconstruction needed there.

| | total R | max drawdown | worst single day |
|---|---|---|---|
| Trend-following alone | +13.1R | -6.2R | -3.0R |
| OR sweep/breakout alone | +136.8R | -27.1R | -1.0R |
| **Combined** | **+149.9R** | **-29.2R** | **-4.0R** |

**Combining adds the returns but does not reduce the worst case - if
anything the combined max drawdown is slightly worse than OR alone.**
Daily correlation between the two is near zero in ordinary conditions
(0.06), so on a typical day they really do behave independently. But the
worst days are tail-correlated: the single worst combined day (-4.0R)
was 2020-03-13, the depth of the COVID volatility shock, when
trend-following's 2xATR stop got blown through by a huge one-day range
(-3.0R alone) on the same day the OR range whipsawed (-1.0R). Extreme
volatility breaks both systems' assumptions at once (a stop sized to
recent ATR stops meaning anything once ATR itself explodes; an opening
range becomes meaningless when the whole day is one giant range), so
this isn't a coincidence - it's a shared structural weakness.

**What this means for sizing a combined system:** don't budget for a
combined bad day as if the two were independent (which would suggest
something like sqrt(1^2+3^2)≈3.2R via naive diversification math) -
budget for the observed worst case, roughly -4R in a single day, which
has actually happened once in 5 years. The extra return from running
both is real and the day-to-day diversification is real, but it doesn't
buy protection against the specific scenario (extremely violent, fast
markets) most likely to blow through a daily-loss limit on a challenge
account.

## Asia/London sweep -> CISD -> {FVG | IFVG | MSS} direct entry, on M3 (`src/strategies/sweep_cisd_direct.py`)

User's request: sweep only the two "major liquidity" sessions (Asia,
London H/L - not the fuller PDH/PDL/OR/M15 set core_reversal.py sweeps),
then persistent CISD, then enter AT MARKET the moment one of three
second conditions confirms - no 50% retracement wait. Three independent
modes, all on 3-minute bars:

| mode | in-sample PF | out-of-sample PF | combined n | combined PF | combined total R |
|---|---|---|---|---|---|
| **FVG** (fresh directional FVG) | 1.03 | **1.51** | 911 | **1.30** | +168.0R |
| **IFVG** (inverted FVG fires) | 1.46 | 1.37 | 867 | **1.41** | **+222.8R** |
| MSS (plain structure break) | 0.89 | 1.08 | 817 | 1.00 | -0.8R |

**FVG and IFVG are, on a trades-tested basis, the strongest results in
this entire repo.** Both cleared PF > 1 in both independent 2-3 year
windows on ~900 trades each (order of magnitude more trades than the
trend-following system, comparable to the OR sweep/breakout's sample).
IFVG's PF actually improved slightly out of sample after already looking
good in-sample (1.46 -> 1.37, both strong); FVG's out-of-sample PF
(1.51) was *better* than in-sample (1.03) - unusual, but the point of
testing both windows is exactly to catch this kind of window-dependence
either direction, and neither window was bad enough to worry about.
**MSS did not hold up as an independent edge** - it flipped from
losing (PF 0.89) to breakeven (PF 1.08), landing at essentially exactly
1.00 combined (917 trades, -0.8R total) with a very large max drawdown
(-78.5R) relative to that near-zero return - a losing/flat rather than
winning confirmation type, and its drawdown-to-return ratio is the worst
of anything tested here. Drop MSS as a standalone trigger; FVG and IFVG
are the two worth taking seriously.

Mechanically, this shares the sweep+CISD skeleton with `core_reversal.py`
but is a materially different strategy: only 2 sweep sources instead of
7, no 50% retracement stage (direct market entry the instant CISD + the
second condition confirm), and a short `confirm_lookahead` window (6
bars) rather than `core_reversal`'s much longer POI validity - a faster,
more aggressive, higher-frequency version of the same idea, on a faster
timeframe (M3 vs M5). That it does markedly better than `core_reversal.py`
(which came out at PF~1.0, no edge) on the same underlying sweep+CISD
concept is a genuinely interesting result in its own right - the
retracement-wait and the wider sweep-source set may be diluting a real
signal rather than refining it.

Trade logs: `reports/sweepcisd_fvg_*.csv`, `reports/sweepcisd_ifvg_*.csv`,
`reports/sweepcisd_mss_*.csv` (both windows each).

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
