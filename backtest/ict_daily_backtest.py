#!/usr/bin/env python3
"""
Backtest du moteur STRICT de "ICT + Checklist Pre-Trade" (pine/ICT_Checklist_ALLINONE_v2.4.pine)
sur des donnees de marche REELLES, en bougies JOURNALIERES.

POURQUOI JOURNALIER ET PAS M5 :
L'indicateur Pine est concu pour du M5 NASDAQ (The5ers). Cet environnement
d'execution n'a pas d'acces reseau vers les fournisseurs de donnees intraday
(Yahoo Finance, stooq, Alpha Vantage, Twelve Data, Finnhub... tous bloques par
la politique reseau du sandbox, verifie via des requetes directes qui
retournent 403). Le seul acces sortant autorise vers des donnees de marche
librement redistribuables passe par des CSV publics hebergés sur GitHub, qui
n'existent qu'en frequence journaliere pour un historique long. Ce script
utilise donc de VRAIES donnees (AAPL, cote au NASDAQ, historique 1984-2008,
source Yahoo Finance via le jeu de donnees d'exemple de matplotlib) mais
adapte le moteur strict a l'echelle de la bougie journaliere :

  Concept M5 (Pine)                  ->  Adaptation journaliere (ce script)
  ----------------------------------------------------------------------
  Biais de structure H1              ->  Biais de structure sur la bougie
                                          journaliere elle-meme (pivots HH/HL
                                          persistants + BOS), faute de
                                          timeframe superieur pertinent.
  Sweep PDH/PDL, Asie, swing M15     ->  Sweep du dernier swing pivot non
                                          balaye (meme algorithme que le
                                          module "Sweep of Liquidity" du
                                          Pine, applique aux swings
                                          journaliers).
  CISD (retournement de direction     ->  Identique : algorithme de run
  des bougies)                            close>open / close<open inchange.
  POI (OB de deplacement ou IFVG)    ->  Identique : bougie de deplacement
                                          (ATR) juste apres le CISD, ou IFVG
                                          plus tardif, meme fenetre d'age.
  Retour a 50% du POI                ->  Identique.
  Sessions NY/Londres, news, M5-only ->  Non applicable a la bougie
                                          journaliere : desactive.
  Max trades/jour, coupe-circuits    ->  Un seul trade ouvert a la fois ;
  perte/drawdown                          coupe-circuits perte journaliere
                                          et drawdown total conserves au
                                          niveau du capital simule.

LIMITES A GARDER EN TETE EN LISANT LES RESULTATS :
  - Ce n'est PAS un backtest fidele de l'indicateur M5 original : la logique
    de session, l'urgence intrabougie (M5) et les niveaux PDH/PDL/Asie sont
    remplaces par un mecanisme de sweep de swing generique.
  - Un seul titre (AAPL), sur une seule periode historique (1984-2008) :
    echantillon de marche particulier, pas un indice NASDAQ diversifie.
  - L'execution suppose un remplissage exact aux prix Entree/SL/TP1/TP2 des
    lors que le High/Low du jour les touche, sans slippage ni frais.
  - En cas de SL et TP touches le meme jour, on suppose le pire cas (SL
    d'abord) : hypothese conservatrice standard en absence de donnees
    intrabougie.

Pour un vrai backtest M5 fidele a l'indicateur, fournir un CSV OHLCV M5 reel
(export TradingView/MT5/broker) : le meme moteur peut tourner dessus, seule
la fenetre de session (NY/Londres) et les niveaux PDH/PDL/Asie redeviennent
pertinents.

Usage:
    python3 ict_daily_backtest.py --csv data/AAPL_1984_2008_daily.csv \
        --capital 5000 --risk-pct 0.25 --min-rr 3.0 --out out/
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Chargement des donnees
# ---------------------------------------------------------------------------

def load_ohlcv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    date_col = "date" if "date" in df.columns else df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.rename(columns={date_col: "date"})
    df = df.sort_values("date").reset_index(drop=True)
    keep = ["date", "open", "high", "low", "close"]
    if "volume" in df.columns:
        keep.append("volume")
    else:
        df["volume"] = 0.0
        keep.append("volume")
    return df[keep]


# ---------------------------------------------------------------------------
# Pivots (equivalent ta.pivothigh / ta.pivotlow, confirmes avec `length`
# bougies de retard des deux cotes -- non-repaint, comme dans le Pine)
# ---------------------------------------------------------------------------

def compute_pivots(high: np.ndarray, low: np.ndarray, length: int):
    n = len(high)
    ph = np.full(n, np.nan)
    pl = np.full(n, np.nan)
    for i in range(length, n - length):
        w_h = high[i - length : i + length + 1]
        if high[i] == w_h.max():
            ph[i] = high[i]
        w_l = low[i - length : i + length + 1]
        if low[i] == w_l.min():
            pl[i] = low[i]
    return ph, pl


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    n = len(high)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    out = np.full(n, np.nan)
    if n >= length:
        out[length - 1] = tr[:length].mean()
        for i in range(length, n):
            out[i] = (out[i - 1] * (length - 1) + tr[i]) / length
    return out


# ---------------------------------------------------------------------------
# Biais de structure (traduction directe de f_coreStructure du Pine)
# ---------------------------------------------------------------------------

@dataclass
class StructureState:
    last_ph: float = np.nan
    prev_ph: float = np.nan
    last_pl: float = np.nan
    prev_pl: float = np.nan
    persistent_bias: int = 0
    last_ph_id: int = -1
    last_pl_id: int = -1
    broken_ph_id: int = -1
    broken_pl_id: int = -1
    dealing_low: float = np.nan
    dealing_high: float = np.nan
    dealing_dir: int = 0
    bos_serial: int = 0


def step_structure(st: StructureState, i: int, ph_i: float, pl_i: float, length: int,
                    close_i: float, high_i: float, low_i: float, allow_fallback: bool) -> bool:
    """Avance la state machine de structure d'une bougie. Retourne True si un BOS vient d'etre marque."""
    if not np.isnan(ph_i):
        st.prev_ph = st.last_ph
        st.last_ph = ph_i
        st.last_ph_id = i - length
    if not np.isnan(pl_i):
        st.prev_pl = st.last_pl
        st.last_pl = pl_i
        st.last_pl_id = i - length

    enough = not np.isnan(st.prev_ph) and not np.isnan(st.prev_pl)
    bias_before = st.persistent_bias
    if enough and st.last_ph > st.prev_ph and st.last_pl > st.prev_pl:
        st.persistent_bias = 1
    elif enough and st.last_ph < st.prev_ph and st.last_pl < st.prev_pl:
        st.persistent_bias = -1
    bias_changed = st.persistent_bias != bias_before

    bull_break = st.last_ph_id != -1 and st.last_ph_id != st.broken_ph_id and not np.isnan(st.last_pl) and close_i > st.last_ph
    bear_break = st.last_pl_id != -1 and st.last_pl_id != st.broken_pl_id and not np.isnan(st.last_ph) and close_i < st.last_pl

    bos_now = False
    if bull_break:
        st.dealing_low = min(st.last_pl, low_i)
        st.dealing_high = high_i
        st.dealing_dir = 1
        st.broken_ph_id = st.last_ph_id
        st.bos_serial += 1
        bos_now = True
    elif bear_break:
        st.dealing_low = low_i
        st.dealing_high = max(st.last_ph, high_i)
        st.dealing_dir = -1
        st.broken_pl_id = st.last_pl_id
        st.bos_serial += 1
        bos_now = True
    elif allow_fallback and enough and st.persistent_bias != 0 and (
        bias_changed or st.dealing_dir != st.persistent_bias or np.isnan(st.dealing_low) or np.isnan(st.dealing_high)
    ):
        st.dealing_low = min(st.last_pl, st.last_ph)
        st.dealing_high = max(st.last_pl, st.last_ph)
        st.dealing_dir = st.persistent_bias
    elif st.dealing_dir == 1 and not np.isnan(st.dealing_high):
        st.dealing_high = max(st.dealing_high, high_i)
    elif st.dealing_dir == -1 and not np.isnan(st.dealing_low):
        st.dealing_low = min(st.dealing_low, low_i)
    return bos_now


# ---------------------------------------------------------------------------
# Parametres du backtest (equivalents des inputs Pine les plus pertinents en
# adaptation journaliere)
# ---------------------------------------------------------------------------

@dataclass
class Params:
    struct_pivot_len: int = 2          # h1PivotLen
    struct_allow_fallback: bool = True  # h1RangeMode == "BOS puis structure"
    swing_len: int = 5                  # swingLen (M15 -> swing journalier)
    sweep_max_age: int = 36
    cisd_max_age: int = 20
    poi_max_age: int = 36
    fvg_keep_age: int = 60
    body_atr_min: float = 0.50
    range_atr_min: float = 1.20
    min_stop_atr: float = 0.50
    tp1_r: float = 1.50
    min_rr: float = 3.0
    stop_buffer_pct: float = 0.0005     # remplace slBufTicks (pas de "tick" generique sur une action)
    capital: float = 5000.0
    risk_pct: float = 0.25
    daily_loss_cap_pct: float = 1.50
    total_dd_cap_pct: float = 8.0


@dataclass
class Trade:
    direction: int
    entry_date: pd.Timestamp
    entry_bar: int
    entry: float
    stop: float
    tp1: float
    tp2: float
    liquidity: float
    exit_date: pd.Timestamp = None
    exit_bar: int = -1
    exit_price: float = np.nan
    exit_reason: str = ""
    r_multiple: float = np.nan
    pnl: float = np.nan


def find_liquidity_target(direction: int, entry: float, stop: float, min_rr: float, swings_above, swings_below):
    risk = abs(entry - stop)
    if risk <= 0:
        return np.nan
    if direction == 1:
        threshold = entry + min_rr * risk
        candidates = [lvl for lvl in swings_above if lvl >= threshold]
        return min(candidates) if candidates else np.nan
    else:
        threshold = entry - min_rr * risk
        candidates = [lvl for lvl in swings_below if lvl <= threshold]
        return max(candidates) if candidates else np.nan


def run_backtest(df: pd.DataFrame, p: Params):
    n = len(df)
    date = df["date"].values
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)

    # Les pivots calcules par compute_pivots() sont centres : ph[j] n'est
    # connaissable qu'une fois la bougie j+length atteinte (comme
    # ta.pivothigh/pivotlow en Pine, non-repaint). On decale donc la serie de
    # `length` bougies avant de l'utiliser au pas de temps i, pour ne jamais
    # regarder dans le futur pendant le backtest.
    def confirmed_series(raw: np.ndarray, length: int) -> np.ndarray:
        out = np.full(len(raw), np.nan)
        if length < len(raw):
            out[length:] = raw[: len(raw) - length]
        return out

    ph_raw, pl_raw = compute_pivots(h, l, p.struct_pivot_len)
    ph, pl = confirmed_series(ph_raw, p.struct_pivot_len), confirmed_series(pl_raw, p.struct_pivot_len)
    sph_raw, spl_raw = compute_pivots(h, l, p.swing_len)
    sph, spl = confirmed_series(sph_raw, p.swing_len), confirmed_series(spl_raw, p.swing_len)
    atr14 = atr(h, l, c, 14)

    struct = StructureState()

    # Swings de liquidite actifs (equivalent hLvl/lLvl)
    swing_highs: list[float] = []
    swing_lows: list[float] = []

    # FVG strict (equivalent coreFvgTops/Bots/Dirs/Bars)
    fvg_top, fvg_bot, fvg_dir, fvg_bar = [], [], [], []

    # CISD run tracking
    cur_dir = 0
    run_first_open = np.nan

    # Sequence long/short (equivalent lStage/sStage)
    l_stage = s_stage = 0
    l_sweep_bar = s_sweep_bar = -1
    l_sweep_extreme = s_sweep_extreme = np.nan
    l_cisd_bar = s_cisd_bar = -1
    l_poi_bar = s_poi_bar = -1
    l_poi_top = l_poi_bot = s_poi_top = s_poi_bot = np.nan
    l_limit = s_limit = np.nan
    l_stop = s_stop = np.nan

    trades: list[Trade] = []
    open_trade: Trade | None = None

    equity = p.capital
    equity_curve = np.zeros(n)
    peak_equity = p.capital
    day_start_equity = p.capital
    current_day = None

    for i in range(n):
        # --- reset journalier des compteurs de risque (equivalent newCalendarDayCore) ---
        d = pd.Timestamp(date[i]).normalize()
        if current_day is None or d != current_day:
            current_day = d
            day_start_equity = equity
        daily_loss_pct = max(0.0, (day_start_equity - equity) / p.capital * 100.0)
        total_dd_pct = max(0.0, (peak_equity - equity) / p.capital * 100.0)
        risk_limits_ok = daily_loss_pct < p.daily_loss_cap_pct and total_dd_pct < p.total_dd_cap_pct

        # --- gestion du trade ouvert (SL/TP1/TP2, pire cas si les deux sont touches le meme jour) ---
        if open_trade is not None:
            tr = open_trade
            hit_sl = (h[i] >= tr.stop) if tr.direction == -1 else (l[i] <= tr.stop)
            hit_tp2 = (h[i] >= tr.tp2) if tr.direction == 1 else (l[i] <= tr.tp2)
            if hit_sl:
                tr.exit_price = tr.stop
                tr.exit_reason = "SL"
            elif hit_tp2:
                tr.exit_price = tr.tp2
                tr.exit_reason = "TP2"
            if tr.exit_reason:
                tr.exit_date = pd.Timestamp(date[i])
                tr.exit_bar = i
                risk_amt = equity * p.risk_pct / 100.0
                r = (tr.exit_price - tr.entry) / (tr.entry - tr.stop) if tr.direction == 1 else (tr.entry - tr.exit_price) / (tr.stop - tr.entry)
                tr.r_multiple = r
                tr.pnl = risk_amt * r
                equity += tr.pnl
                peak_equity = max(peak_equity, equity)
                trades.append(tr)
                open_trade = None

        equity_curve[i] = equity

        # --- mise a jour des pivots de structure (biais) ---
        bos = step_structure(struct, i, ph[i], pl[i], p.struct_pivot_len, c[i], h[i], l[i], p.struct_allow_fallback)
        bias = struct.persistent_bias
        range_aligned = bias != 0 and struct.dealing_dir == bias
        range_low = min(struct.dealing_low, struct.dealing_high) if range_aligned else np.nan
        range_high = max(struct.dealing_low, struct.dealing_high) if range_aligned else np.nan
        mid = (range_low + range_high) / 2.0 if not np.isnan(range_low) else np.nan

        # --- swings de liquidite : enregistrement + detection de sweep ---
        if not np.isnan(sph[i]):
            swing_highs.append(sph[i])
            if len(swing_highs) > 50:
                swing_highs.pop(0)
        if not np.isnan(spl[i]):
            swing_lows.append(spl[i])
            if len(swing_lows) > 50:
                swing_lows.pop(0)

        sweep_buy = False   # sweep d'un plus haut -> setup SHORT
        sweep_sell = False  # sweep d'un plus bas  -> setup LONG
        for lvl in list(swing_highs):
            if h[i] > lvl:
                if c[i] < lvl:
                    sweep_buy = True
                swing_highs.remove(lvl)
        for lvl in list(swing_lows):
            if l[i] < lvl:
                if c[i] > lvl:
                    sweep_sell = True
                swing_lows.remove(lvl)

        # --- CISD (identique a l'algo Pine) ---
        this_dir = 1 if c[i] > o[i] else (-1 if c[i] < o[i] else cur_dir)
        cisd_bull = cisd_bear = False
        if this_dir != cur_dir and this_dir != 0:
            if this_dir == 1 and cur_dir == -1 and not np.isnan(run_first_open) and c[i] > run_first_open:
                cisd_bull = True
            elif this_dir == -1 and cur_dir == 1 and not np.isnan(run_first_open) and c[i] < run_first_open:
                cisd_bear = True
            cur_dir = this_dir
            run_first_open = o[i]

        # --- FVG strict + inversion (IFVG) ---
        bull_ifvg = bear_ifvg = False
        bull_ifvg_top = bull_ifvg_bot = bear_ifvg_top = bear_ifvg_bot = np.nan
        if i >= 2:
            if l[i] > h[i - 2]:
                fvg_top.append(l[i]); fvg_bot.append(h[i - 2]); fvg_dir.append(1); fvg_bar.append(i)
            if h[i] < l[i - 2]:
                fvg_top.append(l[i - 2]); fvg_bot.append(h[i]); fvg_dir.append(-1); fvg_bar.append(i)
        z = len(fvg_bar) - 1
        while z >= 0:
            too_old = i - fvg_bar[z] > p.fvg_keep_age
            bull_invert = (not too_old) and i > fvg_bar[z] and fvg_dir[z] == -1 and c[i] > fvg_top[z]
            bear_invert = (not too_old) and i > fvg_bar[z] and fvg_dir[z] == 1 and c[i] < fvg_bot[z]
            if bull_invert and not bull_ifvg:
                bull_ifvg, bull_ifvg_top, bull_ifvg_bot = True, fvg_top[z], fvg_bot[z]
            if bear_invert and not bear_ifvg:
                bear_ifvg, bear_ifvg_top, bear_ifvg_bot = True, fvg_top[z], fvg_bot[z]
            if too_old or bull_invert or bear_invert:
                del fvg_top[z]; del fvg_bot[z]; del fvg_dir[z]; del fvg_bar[z]
            z -= 1

        displacement = (not np.isnan(atr14[i])) and (
            abs(c[i] - o[i]) >= p.body_atr_min * atr14[i] or (h[i] - l[i]) >= p.range_atr_min * atr14[i]
        )

        # --- expiration des sequences en cours ---
        if (l_stage == 1 and i - l_sweep_bar > p.sweep_max_age) or \
           (l_stage == 2 and (i - l_sweep_bar > p.sweep_max_age or i - l_cisd_bar > p.cisd_max_age)) or \
           (l_stage == 3 and i - l_poi_bar > p.poi_max_age):
            l_stage, l_limit = 0, np.nan
        if (s_stage == 1 and i - s_sweep_bar > p.sweep_max_age) or \
           (s_stage == 2 and (i - s_sweep_bar > p.sweep_max_age or i - s_cisd_bar > p.cisd_max_age)) or \
           (s_stage == 3 and i - s_poi_bar > p.poi_max_age):
            s_stage, s_limit = 0, np.nan

        # --- etape 1 : sweep externe ---
        if sweep_sell:
            l_stage, l_sweep_bar, l_sweep_extreme, l_limit = 1, i, l[i], np.nan
        if sweep_buy:
            s_stage, s_sweep_bar, s_sweep_extreme, s_limit = 1, i, h[i], np.nan

        # --- etape 2 : CISD directionnel apres le sweep ---
        long_cisd_adv = short_cisd_adv = False
        if l_stage == 1 and cisd_bull and i > l_sweep_bar:
            l_stage, l_cisd_bar, long_cisd_adv = 2, i, True
        if s_stage == 1 and cisd_bear and i > s_sweep_bar:
            s_stage, s_cisd_bar, short_cisd_adv = 2, i, True

        # --- etape 3 : POI (OB de deplacement immediat, ou IFVG plus tard) ---
        if displacement and long_cisd_adv:
            l_stage, l_poi_bar, l_poi_top, l_poi_bot, l_limit = 3, i, h[i - 1], l[i - 1], np.nan
        if displacement and short_cisd_adv:
            s_stage, s_poi_bar, s_poi_top, s_poi_bot, s_limit = 3, i, h[i - 1], l[i - 1], np.nan
        if l_stage == 2 and bull_ifvg and i > l_cisd_bar:
            l_stage, l_poi_bar, l_poi_top, l_poi_bot, l_limit = 3, i, bull_ifvg_top, bull_ifvg_bot, np.nan
        if s_stage == 2 and bear_ifvg and i > s_cisd_bar:
            s_stage, s_poi_bar, s_poi_top, s_poi_bot, s_limit = 3, i, bear_ifvg_top, bear_ifvg_bot, np.nan

        # --- calcul du plan (entree 50%, stop, cibles) des que le POI existe ---
        if l_stage == 3 and np.isnan(l_limit):
            cand_limit = (l_poi_top + l_poi_bot) / 2.0
            struct_stop = l_sweep_extreme - p.stop_buffer_pct * cand_limit
            vol_stop = cand_limit - p.min_stop_atr * atr14[i] if not np.isnan(atr14[i]) else struct_stop
            cand_stop = min(struct_stop, vol_stop)
            cand_liq = find_liquidity_target(1, cand_limit, cand_stop, p.min_rr, swing_highs, [])
            bias_ok = bias == 1
            pd_ok = (not np.isnan(mid)) and cand_limit <= mid
            geom_ok = cand_stop < cand_limit and not np.isnan(cand_liq)
            if bias_ok and pd_ok and geom_ok:
                l_limit, l_stop = cand_limit, cand_stop
                l_tp1 = cand_limit + p.tp1_r * (cand_limit - cand_stop)
                l_tp2 = cand_limit + p.min_rr * (cand_limit - cand_stop)
                l_liq = cand_liq
            else:
                l_stage = 0
        if s_stage == 3 and np.isnan(s_limit):
            cand_limit = (s_poi_top + s_poi_bot) / 2.0
            struct_stop = s_sweep_extreme + p.stop_buffer_pct * cand_limit
            vol_stop = cand_limit + p.min_stop_atr * atr14[i] if not np.isnan(atr14[i]) else struct_stop
            cand_stop = max(struct_stop, vol_stop)
            cand_liq = find_liquidity_target(-1, cand_limit, cand_stop, p.min_rr, [], swing_lows)
            bias_ok = bias == -1
            pd_ok = (not np.isnan(mid)) and cand_limit >= mid
            geom_ok = cand_limit < cand_stop and not np.isnan(cand_liq)
            if bias_ok and pd_ok and geom_ok:
                s_limit, s_stop = cand_limit, cand_stop
                s_tp1 = cand_limit - p.tp1_r * (cand_stop - cand_limit)
                s_tp2 = cand_limit - p.min_rr * (cand_stop - cand_limit)
                s_liq = cand_liq
            else:
                s_stage = 0

        # --- etape 4 : retour a 50% -> GO (une seule position ouverte a la fois) ---
        if open_trade is None and risk_limits_ok:
            if l_stage == 3 and i > l_poi_bar and l[i] <= l_limit <= h[i]:
                open_trade = Trade(1, pd.Timestamp(date[i]), i, l_limit, l_stop, l_tp1, l_tp2, l_liq)
                l_stage, s_stage = 4, 0
            elif s_stage == 3 and i > s_poi_bar and l[i] <= s_limit <= h[i]:
                open_trade = Trade(-1, pd.Timestamp(date[i]), i, s_limit, s_stop, s_tp1, s_tp2, s_liq)
                s_stage, l_stage = 4, 0

    return trades, equity_curve


def summarize(trades: list[Trade], equity_curve: np.ndarray, capital: float) -> dict:
    if not trades:
        return {"n_trades": 0}
    r = np.array([t.r_multiple for t in trades])
    pnl = np.array([t.pnl for t in trades])
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    running_max = np.maximum.accumulate(equity_curve)
    dd = (running_max - equity_curve) / running_max * 100.0
    return {
        "n_trades": len(trades),
        "win_rate_pct": 100.0 * len(wins) / len(trades),
        "avg_r": float(r.mean()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if losses.sum() != 0 else np.inf,
        "expectancy_r": float(r.mean()),
        "total_pnl": float(pnl.sum()),
        "final_equity": float(equity_curve[-1]),
        "total_return_pct": float((equity_curve[-1] / capital - 1.0) * 100.0),
        "max_drawdown_pct": float(dd.max()),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "longs": int(sum(1 for t in trades if t.direction == 1)),
        "shorts": int(sum(1 for t in trades if t.direction == -1)),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, help="Fichier OHLCV (colonnes date,open,high,low,close[,volume])")
    ap.add_argument("--capital", type=float, default=5000.0)
    ap.add_argument("--risk-pct", type=float, default=0.25)
    ap.add_argument("--min-rr", type=float, default=3.0)
    ap.add_argument("--swing-len", type=int, default=5)
    ap.add_argument("--out", default="backtest/out")
    args = ap.parse_args()

    df = load_ohlcv(args.csv)
    p = Params(capital=args.capital, risk_pct=args.risk_pct, min_rr=args.min_rr, swing_len=args.swing_len)
    trades, equity_curve = run_backtest(df, p)
    stats = summarize(trades, equity_curve, p.capital)

    os.makedirs(args.out, exist_ok=True)
    trades_df = pd.DataFrame([{
        "direction": "LONG" if t.direction == 1 else "SHORT",
        "entry_date": t.entry_date, "entry": t.entry, "stop": t.stop,
        "tp1": t.tp1, "tp2": t.tp2, "liquidity": t.liquidity,
        "exit_date": t.exit_date, "exit_price": t.exit_price, "exit_reason": t.exit_reason,
        "r_multiple": t.r_multiple, "pnl": t.pnl,
    } for t in trades])
    trades_csv = os.path.join(args.out, "trades.csv")
    trades_df.to_csv(trades_csv, index=False)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(10, 4))
        ax.plot(df["date"], equity_curve)
        ax.set_title(f"Equity curve — {os.path.basename(args.csv)} (n={stats.get('n_trades', 0)} trades)")
        ax.set_ylabel("Equity ($)")
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "equity_curve.png"), dpi=130)
    except Exception as e:
        print(f"(graphique ignore : {e})")

    print("=" * 70)
    print(f"Source          : {args.csv}")
    print(f"Bougies         : {len(df)}  ({df['date'].iloc[0].date()} -> {df['date'].iloc[-1].date()})")
    print(f"Capital initial : {p.capital:.2f}$  risque/trade: {p.risk_pct}%  RR min: {p.min_rr}")
    print("-" * 70)
    for k, v in stats.items():
        print(f"{k:20s}: {v}")
    print("=" * 70)
    print(f"Journal des trades -> {trades_csv}")


if __name__ == "__main__":
    main()
