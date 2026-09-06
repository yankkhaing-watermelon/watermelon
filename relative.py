"""Cross-sectional layer: relative strength and market regime.

Everything in ``screener.py`` judges one symbol against its own history. That is
why a market-wide advance makes all six strategies fire on the same names at the
same time — an absolute rule cannot tell "this stock is strong" from "everything
is strong". This module adds the two columns that supply the missing context:

``rs_rank``     cross-sectional percentile (0-100) of trailing return, computed
                per date across whatever symbols have data on that date. This is
                the column ``config.STRATEGIES["meta_leader"]["rs_column"]``
                already names and that nothing previously produced, so the
                leadership gate silently fell through to an absolute proxy.

``regime_ok``   a boolean market gate. Breadth — the share of the universe
                holding above its own long moving average — is used rather than
                an index level, so no extra data source is needed and the gate
                cannot be skewed by a handful of heavyweight constituents.
                ``breadth_pct`` is written alongside it for inspection.

Both are strictly point-in-time. A percentile on date D ranks only values known
on date D, and breadth is smoothed with a trailing mean, so no future bar can
influence an earlier signal. Both attach to the same enriched frames the
screener reads, in one pre-pass, so the live scan and the backtest cannot drift.

Survivorship caveat: breadth is measured across the universe as it is fetched
today, so it slightly overstates historical health. It is a coarse on/off gate,
which tolerates that better than a return calculation would.
"""
from __future__ import annotations

import pandas as pd

import config

RS = getattr(config, "RELATIVE", {})
RS_LOOKBACK = int(RS.get("rs_lookback", 126))
RS_MIN_NAMES = int(RS.get("rs_min_names", 20))
REGIME_MA = str(RS.get("regime_ma_column", "ema200"))
REGIME_MIN_BREADTH = float(RS.get("regime_min_breadth_pct", 45.0))
REGIME_SMOOTH = int(RS.get("regime_smooth_bars", 5))


def _wide(enriched: dict[str, pd.DataFrame], column: str) -> pd.DataFrame:
    """Align one column across every symbol into a date x symbol frame."""
    series = {}
    for symbol, frame in enriched.items():
        if column in frame.columns:
            s = frame[column]
            series[symbol] = s[~s.index.duplicated(keep="last")]
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


def attach_rs_rank(enriched: dict[str, pd.DataFrame],
                   lookback: int = RS_LOOKBACK,
                   min_names: int = RS_MIN_NAMES) -> None:
    """Write an ``rs_rank`` column (0-100 percentile) into each frame in place.

    The ranked quantity is the trailing ``lookback``-session return. Dates with
    fewer than ``min_names`` symbols reporting are left NaN rather than ranked
    against a handful of names, and every check degrades to its absolute proxy
    when the value is missing.
    """
    closes = _wide(enriched, "close")
    if closes.empty:
        return
    past = closes.shift(lookback)
    ret = (closes / past - 1.0).where(past > 0)
    ranked = ret.rank(axis=1, pct=True) * 100.0
    ranked = ranked.where(ret.notna().sum(axis=1) >= min_names)
    for symbol, frame in enriched.items():
        if symbol in ranked.columns:
            frame["rs_rank"] = ranked[symbol].reindex(frame.index)


def attach_regime(enriched: dict[str, pd.DataFrame],
                  ma_column: str = REGIME_MA,
                  min_breadth_pct: float = REGIME_MIN_BREADTH,
                  smooth_bars: int = REGIME_SMOOTH) -> None:
    """Write ``breadth_pct`` and ``regime_ok`` into each frame in place.

    Breadth is the share of reporting symbols closing above ``ma_column``. It is
    smoothed with a trailing mean so the gate does not flicker on and off around
    the threshold, which would otherwise churn signals for no economic reason.
    """
    closes = _wide(enriched, "close")
    mas = _wide(enriched, ma_column)
    if closes.empty or mas.empty:
        return
    mas = mas.reindex(index=closes.index, columns=closes.columns)
    above = (closes > mas).where(closes.notna() & mas.notna())
    reporting = above.notna().sum(axis=1)
    breadth = 100.0 * above.sum(axis=1) / reporting.where(reporting > 0)
    if smooth_bars > 1:
        breadth = breadth.rolling(smooth_bars, min_periods=1).mean()
    ok = breadth >= min_breadth_pct
    for frame in enriched.values():
        frame["breadth_pct"] = breadth.reindex(frame.index)
        frame["regime_ok"] = ok.reindex(frame.index)


def attach_all(enriched: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Run both pre-passes. Returns the same dict for call-site convenience."""
    if not RS.get("enabled", True):
        return enriched
    attach_rs_rank(enriched)
    attach_regime(enriched)
    return enriched
