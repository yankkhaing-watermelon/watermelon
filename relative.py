"""Cross-sectional layer: relative strength and market regime.

Everything in ``screener.py`` judges one symbol against its own history. That is
why a market-wide advance makes all six strategies fire on the same names at the
same time — an absolute rule cannot tell "this stock is strong" from "everything
is strong". This module adds the two columns that supply the missing context:

``rs_rank``     cross-sectional percentile (0-100) of trailing return, computed
                per date across whatever symbols have data on that date.

``regime_ok``   a nullable boolean market gate. Breadth — the share of the
                universe holding above its own long moving average — is used
                rather than an index level, so no extra data source is needed
                and the gate cannot be skewed by a few heavyweight constituents.
                ``breadth_pct`` is written alongside it for inspection.

Both are strictly point-in-time. A percentile on date D ranks only values known
on date D, and the breadth threshold in percentile mode is a *trailing* quantile
of breadth's own history, so no future bar can influence an earlier signal.

Two failure modes, learned the hard way
---------------------------------------
1. **Missing must not mean "no".** ``breadth >= threshold`` on a NaN breadth
   returns ``False`` in a plain bool Series, which is indistinguishable from a
   genuine risk-off reading and silently empties every gated screener. The gate
   is therefore built on pandas' nullable ``boolean`` dtype: unknown stays
   ``pd.NA``, and ``screener._context_ok`` treats ``NA`` as "no opinion" and
   lets the absolute rules decide.

2. **A hardcoded threshold is a guess.** A fixed 45% breadth floor was never
   checked against this universe's actual distribution; on a market of ~1000
   mostly small-cap counters it can sit above the median and hold the gate shut
   more or less permanently. The default mode is now ``percentile``: the gate
   opens when breadth is above its own trailing quantile, so it adapts to the
   universe instead of encoding an assumption about it. Use
   ``calibrate_regime.py`` before choosing ``fixed``.

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
REGIME_MODE = str(RS.get("regime_mode", "percentile"))
REGIME_PERCENTILE = float(RS.get("regime_percentile", 35.0))
REGIME_LOOKBACK = int(RS.get("regime_lookback_bars", 504))
REGIME_MIN_BARS = int(RS.get("regime_min_bars", 120))

# Filled by ``attach_all`` so the exporter can publish why a scan came back
# empty. An empty screen with no explanation is the bug that hid the bug.
LAST_DIAGNOSTICS: dict = {}


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


def breadth_series(enriched: dict[str, pd.DataFrame],
                   ma_column: str = REGIME_MA,
                   smooth_bars: int = REGIME_SMOOTH) -> pd.Series:
    """Share of reporting symbols closing above ``ma_column``, 0-100.

    Smoothed with a trailing mean so the gate does not flicker on and off around
    the threshold, which would churn signals for no economic reason. Exposed
    separately so ``calibrate_regime.py`` can study the distribution without
    running a scan.
    """
    closes = _wide(enriched, "close")
    mas = _wide(enriched, ma_column)
    if closes.empty or mas.empty:
        return pd.Series(dtype="float64")
    mas = mas.reindex(index=closes.index, columns=closes.columns)
    above = (closes > mas).where(closes.notna() & mas.notna())
    reporting = above.notna().sum(axis=1)
    breadth = 100.0 * above.sum(axis=1) / reporting.where(reporting > 0)
    if smooth_bars > 1:
        breadth = breadth.rolling(smooth_bars, min_periods=1).mean()
    return breadth


def regime_threshold(breadth: pd.Series,
                     mode: str = REGIME_MODE,
                     min_breadth_pct: float = REGIME_MIN_BREADTH,
                     percentile: float = REGIME_PERCENTILE,
                     lookback: int = REGIME_LOOKBACK,
                     min_bars: int = REGIME_MIN_BARS) -> pd.Series:
    """The bar breadth must clear on each date.

    ``fixed``       a constant; calibrate it first with ``calibrate_regime.py``.
    ``percentile``  a trailing quantile of breadth's own history — self-
                    calibrating, and NaN until ``min_bars`` of history exist so
                    early dates are left undecided rather than gated shut.
    ``off``         no threshold; the gate never blocks.
    """
    if breadth.empty or mode == "off":
        return pd.Series(float("nan"), index=breadth.index, dtype="float64")
    if mode == "percentile":
        return breadth.rolling(lookback, min_periods=min_bars).quantile(
            max(0.0, min(1.0, percentile / 100.0)))
    return pd.Series(float(min_breadth_pct), index=breadth.index, dtype="float64")


def attach_regime(enriched: dict[str, pd.DataFrame],
                  ma_column: str = REGIME_MA,
                  min_breadth_pct: float = REGIME_MIN_BREADTH,
                  smooth_bars: int = REGIME_SMOOTH,
                  mode: str = REGIME_MODE,
                  percentile: float = REGIME_PERCENTILE,
                  lookback: int = REGIME_LOOKBACK,
                  min_bars: int = REGIME_MIN_BARS) -> None:
    """Write ``breadth_pct``, ``regime_threshold`` and ``regime_ok`` in place.

    ``regime_ok`` is nullable boolean. Where breadth or its threshold cannot be
    computed the value is ``pd.NA``, not ``False``: the screener then falls back
    to its absolute rules instead of standing the whole book down on missing
    data.
    """
    breadth = breadth_series(enriched, ma_column, smooth_bars)
    if breadth.empty:
        return
    thr = regime_threshold(breadth, mode, min_breadth_pct, percentile,
                           lookback, min_bars)

    ok = pd.Series(pd.NA, index=breadth.index, dtype="boolean")
    decided = breadth.notna() & thr.notna()
    if bool(decided.any()):
        ok[decided] = (breadth[decided] >= thr[decided]).astype("boolean")

    for frame in enriched.values():
        frame["breadth_pct"] = breadth.reindex(frame.index)
        frame["regime_threshold"] = thr.reindex(frame.index)
        frame["regime_ok"] = ok.reindex(frame.index)

    last = breadth.index.max()
    LAST_DIAGNOSTICS.update({
        "mode": mode,
        "as_of": str(getattr(last, "date", lambda: last)()),
        "breadth_pct": None if pd.isna(breadth.loc[last]) else round(float(breadth.loc[last]), 2),
        "threshold_pct": None if pd.isna(thr.loc[last]) else round(float(thr.loc[last]), 2),
        "regime_ok": None if pd.isna(ok.loc[last]) else bool(ok.loc[last]),
        "decided_bars": int(decided.sum()),
        "ma_column": ma_column,
        "smooth_bars": smooth_bars,
    })


def attach_all(enriched: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Run both pre-passes. Returns the same dict for call-site convenience."""
    LAST_DIAGNOSTICS.clear()
    if not RS.get("enabled", True):
        LAST_DIAGNOSTICS.update({"mode": "disabled", "regime_ok": None})
        return enriched
    attach_rs_rank(enriched)
    attach_regime(enriched)

    ranked = sum(1 for f in enriched.values()
                 if "rs_rank" in f.columns and not f.empty
                 and pd.notna(f["rs_rank"].iloc[-1]))
    LAST_DIAGNOSTICS["rs_ranked_symbols"] = ranked
    LAST_DIAGNOSTICS["universe_symbols"] = len(enriched)
    return enriched
