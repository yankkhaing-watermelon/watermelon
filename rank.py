"""
Strength ranking layer.

The screening engine produces *qualification* (which symbols pass each screener)
and the raw indicator values (rsi/adx/vol_ratio/roc10); it explicitly leaves
`score = None`. This module turns those raw values into a 0-100 strength score so
the screened list can be sorted strongest-first.

The three screeners that watermelon shares with the StrategyTerminal repo -
trending, gaining_momentum (Momentum) and meta_leader (M.E.T.A.) - use formulas
byte-for-byte identical to StrategyTerminal/rank.py, so those three produce the
SAME stocks AND the SAME strength ranking in both apps. The other three
(early_uptrend, reversal, base_breakout) are watermelon-only and are scored here
so watermelon can rank its full six-screener list; they intentionally do not
exist in StrategyTerminal. Keep the three shared formulas in sync across both
files.

Formula (three shared with StrategyTerminal + three watermelon-only):

    trending         : 0.45*ADX_n + 0.30*RSI_n  + 0.25*VOL_n
    early_uptrend    : 0.35*VOL_n + 0.30*RSI_n   + 0.20*ROC_n + 0.15*ADX_n
    reversal         : 0.40*ROC_n + 0.30*RSI_n   + 0.30*VOL_n
    gaining_momentum : 0.40*VOL_n + 0.35*ROC_n   + 0.25*ADX_n
    base_breakout    : 0.50*VOL_n + 0.30*ROC_n   + 0.20*ADX_n
    meta_leader      : 0.40*ADX_n + 0.30*HI52_n  + 0.30*VOL_n

Each *_n is a bounded 0-100 scaler (see SCALERS). The weights follow each
screener's economic intent: fresh trends and breakouts lead on volume
confirmation, reversals lead on the momentum flip (ROC/RSI), established trends
and leaders lead on ADX. Tune the anchor points here; if you change any of the
three SHARED formulas, make the identical change in StrategyTerminal/rank.py.

NOTE: these numbers are the engine's own definition. They will NOT reproduce the
exact "Strength" values from the previous workers.dev build, whose formula was
not recoverable. Everything upstream of this file (matches + indicators) is
bit-identical to the kernel.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


# Matches the strength model StrategyTerminal stamps, so the three shared
# screeners report the same model as well as the same scores.
MODEL = "strength-v1.0.0"


# ---- bounded linear scalers: value <= lo -> 0, value >= hi -> 100 ----------
SCALERS = {
    "adx": (15.0, 50.0),   # ADX 15 -> 0, 50 -> 100
    "vol": (1.0, 3.0),     # vol_ratio 1x -> 0, 3x -> 100
    "roc": (0.0, 15.0),    # ROC10 0% -> 0, 15% -> 100
    "rsi": (50.0, 75.0),   # RSI 50 -> 0, 75 -> 100 (position within trend band)
    "hi52": (0.80, 1.00),  # close/52w-high 0.80 -> 0, 1.00 -> 100
}


def _scale(value: Any, key: str) -> float:
    lo, hi = SCALERS[key]
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v) or hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (v - lo) / (hi - lo))) * 100.0


def _hi52_ratio(frame: pd.DataFrame) -> float:
    """close / trailing 252-bar high. 1.0 == at the 52-week high."""
    if frame is None or len(frame) == 0:
        return 0.0
    window = frame["high"].tail(252)
    hi = float(window.max()) if len(window) else 0.0
    close = float(frame["close"].iloc[-1])
    return (close / hi) if hi > 0 else 0.0


def strength(strategy: str, row: dict, frame: pd.DataFrame | None = None) -> float:
    """Return a 0-100 strength score for one hit row under one strategy."""
    adx = _scale(row.get("adx"), "adx")
    vol = _scale(row.get("vol_ratio"), "vol")
    roc = _scale(row.get("roc10"), "roc")
    rsi = _scale(row.get("rsi"), "rsi")

    if strategy == "trending":
        s = 0.45 * adx + 0.30 * rsi + 0.25 * vol
    elif strategy == "early_uptrend":
        # a trend just starting: reward volume confirmation and building momentum
        s = 0.35 * vol + 0.30 * rsi + 0.20 * roc + 0.15 * adx
    elif strategy == "reversal":
        # a momentum flip: reward the re-acceleration (ROC/RSI) plus volume
        s = 0.40 * roc + 0.30 * rsi + 0.30 * vol
    elif strategy == "gaining_momentum":
        s = 0.40 * vol + 0.35 * roc + 0.25 * adx
    elif strategy == "base_breakout":
        # a volume-spike breakout from a base: volume dominates, then thrust
        s = 0.50 * vol + 0.30 * roc + 0.20 * adx
    elif strategy == "meta_leader":
        hi52 = _scale(_hi52_ratio(frame), "hi52")
        s = 0.40 * adx + 0.30 * hi52 + 0.30 * vol
    else:
        # unknown strategy; neutral fallback
        s = (adx + vol) / 2.0

    return round(max(0.0, min(100.0, s)), 1)
