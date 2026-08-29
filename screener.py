"""
The six screening strategies. Each check function evaluates ONE symbol at
ONE bar index ``i``. The live screener calls it at the latest bar and the
backtester calls the same function at every historical bar, so the tested
rules and live rules cannot drift apart.
"""

import pandas as pd
import config
from indicators import enrich


# ---------------------------------------------------------------- helpers
def _liquid_enough(df: pd.DataFrame, i: int, min_value: float) -> bool:
    v = df["avg_value"].iloc[i]
    return pd.notna(v) and v >= min_value


def _crossed_above_recently(
    a: pd.Series,
    b: pd.Series,
    i: int,
    lookback: int,
) -> bool:
    """True when series ``a`` crossed above ``b`` in the latest lookback."""
    start = max(1, i - lookback + 1)
    for j in range(start, i + 1):
        if a.iloc[j - 1] <= b.iloc[j - 1] and a.iloc[j] > b.iloc[j]:
            return True
    return False


def _close_strength(row: pd.Series) -> float:
    """Close location in the daily range: 0.0 at low, 1.0 at high."""
    rng = float(row["high"] - row["low"])
    if rng <= 0:
        return 0.0
    return float((row["close"] - row["low"]) / rng)


# ---------------------------------------------------------------- strategies
def check_trending(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    if pd.isna(row["ema200"]) or pd.isna(row["adx"]):
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    if p["require_ema_stack"]:
        if not (row["close"] > row["ema20"] > row["ema50"] > row["ema200"]):
            return False
    if row["adx"] < p["adx_min"]:
        return False
    if not (p["rsi_min"] <= row["rsi"] <= p["rsi_max"]):
        return False
    return True


def check_early_uptrend(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    if pd.isna(row["ema200"]) or pd.isna(row["vol_ratio"]):
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    if not _crossed_above_recently(
        df["ema20"], df["ema50"], i, p["cross_lookback"]
    ):
        return False
    if p["require_above_ema200"] and row["close"] <= row["ema200"]:
        return False
    if row["vol_ratio"] < p["volume_ratio_min"]:
        return False
    if row["rsi"] < p["rsi_min"]:
        return False
    return True


def check_reversal(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    if pd.isna(row["rsi"]) or pd.isna(row["macd"]):
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    start = max(0, i - p["rsi_lookback"])
    if df["rsi"].iloc[start:i].min() > p["rsi_was_below"]:
        return False
    if row["rsi"] < p["rsi_now_above"]:
        return False
    if p["require_macd_cross"]:
        if row["macd"] <= row["macd_signal"]:
            return False
        if not _crossed_above_recently(
            df["macd"], df["macd_signal"], i, p["macd_cross_lookback"]
        ):
            return False
    if p["require_close_above_ema20"] and row["close"] <= row["ema20"]:
        return False
    return True


def check_gaining_momentum(df: pd.DataFrame, i: int, p: dict) -> bool:
    row = df.iloc[i]
    roc_col = f"roc{p['roc_period']}"
    if pd.isna(row.get(roc_col)) or pd.isna(row["vol_ratio"]):
        return False
    if row["close"] < p["min_price"]:
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False
    if row["vol_ratio"] < p["volume_ratio_min"]:
        return False
    if row[roc_col] < p["roc_min"]:
        return False
    n = p["macd_hist_rising_bars"]
    if i < n:
        return False
    hist = df["macd_hist"].iloc[i - n:i + 1]
    if not all(hist.iloc[k] > hist.iloc[k - 1] for k in range(1, len(hist))):
        return False
    return True


def check_base_breakout(df: pd.DataFrame, i: int, p: dict) -> bool:
    """Quiet low-range base followed by a high-volume expansion bar.

    The volume dry-up BEFORE the spike matters as much as the spike itself.
    A big volume day on its own is common; a big volume day after weeks of
    contracting volume near the lows is the footprint of accumulation once
    supply has been exhausted.
    """
    lb = p["range_lookback"]
    base = p["base_bars"]
    if i < lb + base + 1:
        return False

    row = df.iloc[i]
    prev = df.iloc[i - 1]
    if pd.isna(row["vol_ratio"]) or pd.isna(row.get("ema20")):
        return False
    if row["close"] < p["min_price"]:
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False

    # The BASE sat low in its range, measured on the bar before the spike. A
    # breakout bar jumps by definition, so measuring after it would reject
    # every setup that actually worked.
    window = df.iloc[i - lb:i]
    hi = float(window["high"].max())
    lo = float(window["low"].min())
    if hi <= lo:
        return False
    if (prev["close"] - lo) / (hi - lo) > p["range_position_max"]:
        return False

    # Volume dried up through the base. The spike bar is excluded, or a big
    # enough spike would mask the contraction it is meant to break out of.
    quiet = df["volume"].iloc[i - base:i].mean()
    before = df["volume"].iloc[i - base - base:i - base].mean()
    if pd.isna(quiet) or pd.isna(before) or before <= 0:
        return False
    if quiet / before > p["dryup_ratio_max"]:
        return False

    if row["vol_ratio"] < p["volume_ratio_min"]:
        return False

    # Wide bar closing strong. A volume spike that closes on its low is
    # distribution, not accumulation.
    if prev["close"] <= 0:
        return False
    if (row["close"] / prev["close"] - 1) * 100 < p["min_gain_pct"]:
        return False
    if _close_strength(row) < p["close_strength_min"]:
        return False

    # Reclaimed EMA20 recently — not necessarily yesterday. In a flat base
    # price oscillates across EMA20, so demanding a same-day cross rejects
    # most genuine setups.
    if p["require_reclaim_ema20"]:
        if row["close"] <= row["ema20"]:
            return False
        k = p["reclaim_lookback"]
        past = df.iloc[max(0, i - k):i]
        if not (past["close"] <= past["ema20"]).any():
            return False

    return True


def check_meta_leader(df: pd.DataFrame, i: int, p: dict) -> bool:
    """A leader showing an institutional footprint, still buyable.

    Modelled on the momentum/relative-strength swing approach in the
    O'Neil-Minervini lineage: screen for leadership first, then require a
    volume-and-close signature, and refuse to chase anything already extended.

    Two optional upgrades, both degrade gracefully if absent:

      rs_rank   a cross-sectional percentile written in by a pre-pass. When
                the column exists it replaces the absolute return proxy, which
                is the difference between "this stock went up" and "this stock
                is in the top decile". Without it, the proxy is used.
      ema10     short-term momentum reference. Falls back to EMA20.

    `require_base` switches between the broad screen (leadership + footprint)
    and a tight Minervini-style variant that also demands a shallow base with
    contracting volatility. The tight form is extremely sparse — on test data
    the ATR contraction alone rejects roughly 95% of otherwise-valid bars — so
    it is off by default and should be treated as an experiment, not a setting.
    """
    min_history = int(p.get("min_history", 252))
    if i < min_history - 1:
        return False

    row = df.iloc[i]
    for col in ("ema20", "ema50", "ema200", "atr", "vol_ratio", "avg_value"):
        if pd.isna(row.get(col)):
            return False
    if row["close"] < p["min_price"]:
        return False
    if not _liquid_enough(df, i, p["min_avg_value_myr"]):
        return False

    # ---------------------------------------------------- leadership gate
    rise_lb = int(p["ema50_rise_lookback"])
    if i < rise_lb or pd.isna(df["ema50"].iloc[i - rise_lb]):
        return False
    if not (row["close"] > row["ema50"] > row["ema200"]):
        return False
    if row["ema50"] <= df["ema50"].iloc[i - rise_lb]:
        return False

    # Structural leadership: near the 52-week high regardless of RS method.
    high_252 = float(df["high"].iloc[i - 251:i + 1].max())
    if high_252 <= 0:
        return False
    if float(row["close"]) / high_252 < p["high_252_proximity_min"]:
        return False

    # Relative strength. The advance is measured over the leg ENDING before
    # the recent window, so a consolidation cannot suppress the very measure
    # meant to identify a leader that has earned the right to consolidate.
    look_back_window = int(p["recent_window"])
    rs_col = p.get("rs_column", "rs_rank")
    if rs_col in df.columns and pd.notna(row.get(rs_col)):
        if float(row[rs_col]) < p["rs_rank_min"]:
            return False
    else:
        ret_lb = int(p["return_lookback"])
        anchor = i - look_back_window
        if anchor - ret_lb < 0:
            return False
        c_start = float(df["close"].iloc[anchor - ret_lb])
        c_end = float(df["close"].iloc[anchor])
        if c_start <= 0:
            return False
        if (c_end / c_start - 1.0) * 100.0 < p["return_min_pct"]:
            return False

    # ---------------------------------------------------- still buyable
    if row["close"] > row["ema20"] * p["max_extension_ema20"]:
        return False

    # Short-term momentum intact. EMA10 when the column exists, else EMA20.
    fast_ma = "ema10" if ("ema10" in df.columns and pd.notna(row.get("ema10"))) else "ema20"
    if p.get("require_above_fast_ma", True) and row["close"] <= row[fast_ma]:
        return False

    # ------------------------------------------- institutional footprint
    # Looked for across a short WINDOW, not demanded all on one bar. Requiring
    # a volume spike, a wide gain and a strong close simultaneously on top of a
    # full leadership gate is a conjunction so rare it fires on almost nothing —
    # and it is the wrong reading of the method, which screens for recent
    # character rather than a single perfect bar.
    fw = int(p["footprint_window"])
    start = max(1, i - fw + 1)
    footprint_i = None
    for j in range(i, start - 1, -1):
        bar = df.iloc[j]
        before = df.iloc[j - 1]
        if pd.isna(bar.get("vol_ratio")) or float(before["close"]) <= 0:
            continue
        if float(bar["vol_ratio"]) < p["volume_ratio_min"]:
            continue
        if (float(bar["close"]) / float(before["close"]) - 1.0) * 100.0 < p["gain_min_pct"]:
            continue
        if _close_strength(bar) < p["close_strength_min"]:
            continue
        footprint_i = j
        break
    if footprint_i is None:
        return False

    # The move has to have held. Giving back the footprint bar's gain means
    # the buyers who left that mark have already been absorbed.
    if float(row["close"]) < float(df["close"].iloc[footprint_i]) * p["hold_min"]:
        return False

    # ------------------------------------- optional base confirmation
    if not p.get("require_base", False):
        return True

    # The base is measured up to the FOOTPRINT bar, not up to today. Anchoring
    # it at today would put the footprint bar inside the base, inflating the
    # very high the breakout then has to clear — a contradiction that makes
    # this branch fire on nothing at all.
    lb = look_back_window
    if footprint_i - lb < 0:
        return False
    base = df.iloc[footprint_i - lb:footprint_i]
    base_high = float(base["high"].max())
    base_low = float(base["low"].min())
    if base_high <= 0:
        return False
    if (base_high - base_low) / base_high > p["base_depth_max"]:
        return False

    # Volatility contraction across the base, ending before the footprint bar
    # whose range is large by definition and would mask the contraction.
    fast_n = int(p["atr_fast_bars"])
    slow_n = int(p["atr_slow_bars"])
    if footprint_i - slow_n < 0:
        return False
    atr_fast = df["atr"].iloc[footprint_i - fast_n:footprint_i].mean()
    atr_slow = df["atr"].iloc[footprint_i - slow_n:footprint_i].mean()
    if pd.isna(atr_fast) or pd.isna(atr_slow) or atr_slow <= 0:
        return False
    if atr_fast / atr_slow > p["atr_contraction_max"]:
        return False

    # The footprint bar is what clears the base.
    if float(df["close"].iloc[footprint_i]) <= base_high * p["breakout_buffer"]:
        return False

    return True


CHECKS = {
    "trending": check_trending,
    "early_uptrend": check_early_uptrend,
    "reversal": check_reversal,
    "gaining_momentum": check_gaining_momentum,
    "base_breakout": check_base_breakout,
    "meta_leader": check_meta_leader,
}


# ---------------------------------------------------------------- live scan
def scan(
    data: dict[str, pd.DataFrame],
    strategies: dict | None = None,
) -> dict[str, list[dict]]:
    """Run all enabled strategies on the latest bar of every symbol."""
    strategies = strategies or config.STRATEGIES
    hits: dict[str, list[dict]] = {name: [] for name in strategies}

    for symbol, raw in data.items():
        try:
            df = enrich(raw)
        except Exception:
            continue
        if df.empty:
            continue
        i = len(df) - 1
        for name, params in strategies.items():
            if not params.get("enabled", True):
                continue
            check = CHECKS.get(name)
            if check is None:
                continue
            try:
                if not check(df, i, params):
                    continue
                row = df.iloc[i]
                hits[name].append({
                    "symbol": symbol,
                    "close": round(float(row["close"]), 3),
                    "rsi": round(float(row["rsi"]), 1),
                    "adx": round(float(row["adx"]), 1),
                    "vol_ratio": round(float(row["vol_ratio"]), 2),
                    "roc10": round(float(row["roc10"]), 2),
                })
            except Exception:
                continue

    # strongest volume signals first within each strategy
    for name in hits:
        hits[name].sort(key=lambda r: r["vol_ratio"], reverse=True)
    return hits
