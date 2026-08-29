"""Indicator enrichment for the restored absolute-TA screener.

Rebuilt from standard formulas to supply exactly the columns the six
``check_*`` strategies read: EMAs (10/20/50/200), RSI14 (Wilder), ADX14
(Wilder), MACD 12/26/9, ROC10, ATR14 (Wilder), a 20-day volume ratio, and a
20-day average traded value. ``rs_rank`` is intentionally NOT produced here --
it is an optional cross-sectional column a pre-pass may add; meta_leader falls
back to its return proxy when it is absent.

All indicators are causal (no look-ahead): every value at bar ``i`` uses only
bars <= i.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

EMA_SPANS = (10, 20, 50, 200)
RSI_PERIOD = 14
ADX_PERIOD = 14
ATR_PERIOD = 14
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ROC_PERIOD = 10
VOL_LOOKBACK = 20


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(c[0]) for c in df.columns]
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    if "adj_close" in df and "close" not in df:
        df = df.rename(columns={"adj_close": "close"})
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(df.columns):
        return pd.DataFrame()
    df = df[required].apply(pd.to_numeric, errors="coerce")
    idx = pd.to_datetime(df.index, errors="coerce")
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.isna()].sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df.dropna(subset=["open", "high", "low", "close"])


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder smoothing == EWM with alpha = 1/period.
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def _atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = ADX_PERIOD) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, min_periods=period, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _macd(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    macd = fast - slow
    signal = macd.ewm(span=MACD_SIGNAL, adjust=False).mean()
    return macd, signal, macd - signal


def enrich(raw: pd.DataFrame) -> pd.DataFrame:
    """Return an OHLCV frame with every indicator column the screener reads."""
    df = _normalise(raw)
    if df.empty:
        return df
    close = df["close"]

    for span in EMA_SPANS:
        df[f"ema{span}"] = close.ewm(span=span, adjust=False).mean()

    df["rsi"] = _rsi(close)
    df["adx"] = _adx(df)
    df["atr"] = _atr(df)

    macd, signal, hist = _macd(close)
    df["macd"] = macd
    df["macd_signal"] = signal
    df["macd_hist"] = hist

    df[f"roc{ROC_PERIOD}"] = close.pct_change(ROC_PERIOD) * 100.0

    vol_mean = df["volume"].rolling(VOL_LOOKBACK, min_periods=1).mean()
    df["vol_ratio"] = df["volume"] / vol_mean.replace(0, np.nan)
    df["avg_value"] = (close * df["volume"]).rolling(
        VOL_LOOKBACK, min_periods=1).mean()

    return df
