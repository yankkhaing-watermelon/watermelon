"""Pluggable exit policies for the backtest and the null-model research harness.

Two policies, chosen to reproduce the original A/B exit comparison:

* Policy A -- fixed target/stop: +15% target, -7% stop. On an intrabar tie
  (a bar whose range spans both levels) the STOP is assumed to fill first;
  this is the conservative, non-flattering convention for an honest backtest.

* Policy B -- 3xATR14 chandelier trail: a single rule that handles winners and
  losers alike. Each bar the exit level is

      exit_level = (highest close since entry) - 3 * ATR14

  recomputed with the bar's own ATR14, so it ratchets up as new closing highs
  print and tightens/loosens with volatility. The position is closed on the
  first bar whose close is at or below that level; the fill is assumed at that
  close. At entry the high-water mark is the entry bar's close, so the initial
  stop sits ~3 ATR below entry with no separate floor and no separate target.

Both policies price entries at the next session's open (handled by the caller).
Returns are gross of commission; the caller applies commission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

FIXED_TARGET_PCT = 15.0
FIXED_STOP_PCT = 7.0
FIXED_HOLD_DAYS = 20   # config.BACKTEST hold_days: time stop for the fixed rule
ATR_MULT = 3.0


@dataclass
class ExitResult:
    exit_date: pd.Timestamp
    gross_ret_pct: float
    hold_sessions: int
    reason: str


ExitPolicy = Callable[[pd.DataFrame, float], ExitResult]


def _last(future: pd.DataFrame, entry_price: float, held: int, reason: str) -> ExitResult:
    ts = future.index[-1]
    close = float(future["close"].iloc[-1])
    return ExitResult(ts, (close / entry_price - 1) * 100.0, held, reason)


def policy_fixed(future: pd.DataFrame, entry_price: float) -> ExitResult:
    """Policy A: +15% target / -7% stop, stop-first on intrabar ties."""
    stop = entry_price * (1 - FIXED_STOP_PCT / 100.0)
    target = entry_price * (1 + FIXED_TARGET_PCT / 100.0)
    held = 0
    for ts, bar in future.iterrows():
        held += 1
        low, high = float(bar["low"]), float(bar["high"])
        if low <= stop:                      # stop first on a spanning bar
            return ExitResult(ts, -FIXED_STOP_PCT, held, "stop")
        if high >= target:
            return ExitResult(ts, FIXED_TARGET_PCT, held, "target")
        if held >= FIXED_HOLD_DAYS:
            close = float(bar["close"])
            return ExitResult(ts, (close / entry_price - 1) * 100.0, held, "time")
    return _last(future, entry_price, held, "eod")


CHANDELIER_INITIAL_STOP_PCT = 7.0   # -7% hard floor before the trail takes over
CHANDELIER_HOLD_CAP = 250           # long time cap so dead money eventually exits


def _atr_at(bar) -> float:
    """ATR from whichever column the enriched frame carries (atr or atr14)."""
    for col in ("atr14", "atr"):
        if col in bar.index:
            try:
                return float(bar[col])
            except (TypeError, ValueError):
                return 0.0
    return 0.0


def policy_chandelier_3atr(future: pd.DataFrame, entry_price: float) -> ExitResult:
    """Policy B: -7% initial stop, then trail 3*ATR14 below the highest close.

    Matches the old app's 'ATR trail 3x' button: no profit target; a -7% initial
    floor; the stop trails 3*ATR14 under the highest close since entry and only
    ever ratchets up; a 250-session hold cap. ``future`` must carry an ``atr14``
    column and an ``low`` column (enriched frame). The trail is evaluated on
    closes (fill at the triggering close); the -7% floor is evaluated intrabar
    on the low (fill at the floor).
    """
    hard_floor = entry_price * (1 - CHANDELIER_INITIAL_STOP_PCT / 100.0)
    high_water = entry_price
    held = 0
    for ts, bar in future.iterrows():
        held += 1
        low, close, atr = float(bar["low"]), float(bar["close"]), _atr_at(bar)
        # -7% initial floor caps early losses at -7% no matter how wide ATR is,
        # until the rising trail overtakes it. Checked intrabar on the low first.
        trail_level = high_water - ATR_MULT * atr if atr > 0 else -float("inf")
        effective_stop = max(hard_floor, trail_level)
        if low <= effective_stop:
            ret = -CHANDELIER_INITIAL_STOP_PCT if effective_stop == hard_floor \
                else (effective_stop / entry_price - 1) * 100.0
            reason = "initial_stop" if effective_stop == hard_floor else "trail"
            return ExitResult(ts, ret, held, reason)
        high_water = max(high_water, close)
        if held >= CHANDELIER_HOLD_CAP:
            return ExitResult(ts, (close / entry_price - 1) * 100.0, held, "time_cap")
    return _last(future, entry_price, held, "eod")


POLICIES: dict[str, ExitPolicy] = {
    "fixed_15_7": policy_fixed,
    "chandelier_3atr": policy_chandelier_3atr,
}

POLICY_LABELS: dict[str, str] = {
    "fixed_15_7": "+15% / -7%",
    "chandelier_3atr": "3xATR14 trail",
}
