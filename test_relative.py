"""Tests for the cross-sectional layer.

The point-in-time test is the important one: a percentile is the easiest place
to leak the future, because ranking naturally wants the whole column.
"""
import numpy as np
import pandas as pd

import relative
import screener


def _frame(closes, ema200=None):
    n = len(closes)
    idx = pd.bdate_range("2024-01-01", periods=n)
    df = pd.DataFrame({"close": closes, "open": closes, "high": closes,
                       "low": closes, "volume": [1_000] * n}, index=idx)
    df["ema200"] = ema200 if ema200 is not None else [c * 0.9 for c in closes]
    return df


def _universe(n_symbols=30, n_bars=200, seed=0):
    rng = np.random.default_rng(seed)
    out = {}
    for k in range(n_symbols):
        drift = 1.0 + (k - n_symbols / 2) * 0.0004
        closes = list(np.cumprod(np.full(n_bars, drift)) * 10)
        out[f"S{k}"] = _frame(closes)
    return out


def test_rs_rank_is_a_percentile():
    u = _universe()
    relative.attach_rs_rank(u, lookback=60, min_names=5)
    last = pd.Series({s: f["rs_rank"].iloc[-1] for s, f in u.items()}).dropna()
    assert len(last) == 30
    assert last.min() >= 0 and last.max() <= 100
    # strongest drift should outrank the weakest
    assert last["S29"] > last["S0"]


def test_rs_rank_is_point_in_time():
    """Truncating the future must not change an earlier rs_rank value."""
    full = _universe()
    relative.attach_rs_rank(full, lookback=60, min_names=5)
    cut = {s: f.iloc[:150].copy() for s, f in _universe().items()}
    relative.attach_rs_rank(cut, lookback=60, min_names=5)
    a = full["S7"]["rs_rank"].iloc[149]
    b = cut["S7"]["rs_rank"].iloc[149]
    assert (pd.isna(a) and pd.isna(b)) or abs(a - b) < 1e-9


def test_rs_rank_blank_when_too_few_names():
    u = _universe(n_symbols=3)
    relative.attach_rs_rank(u, lookback=60, min_names=20)
    assert u["S0"]["rs_rank"].isna().all()


def test_breadth_gate_opens_and_closes():
    strong = {f"S{k}": _frame([10.0] * 50, ema200=[9.0] * 50) for k in range(20)}
    relative.attach_regime(strong, min_breadth_pct=45.0, smooth_bars=1)
    assert bool(strong["S0"]["regime_ok"].iloc[-1]) is True
    assert strong["S0"]["breadth_pct"].iloc[-1] == 100.0

    weak = {f"S{k}": _frame([10.0] * 50, ema200=[11.0] * 50) for k in range(20)}
    relative.attach_regime(weak, min_breadth_pct=45.0, smooth_bars=1)
    assert bool(weak["S0"]["regime_ok"].iloc[-1]) is False


def test_context_gate_blocks_on_weak_regime():
    df = _frame([10.0] * 30)
    df["regime_ok"] = False
    assert screener._context_ok(df, 29, {"require_market_regime": True}) is False
    assert screener._context_ok(df, 29, {"require_market_regime": False}) is True


def test_context_gate_blocks_on_low_rs():
    df = _frame([10.0] * 30)
    df["rs_rank"] = 30.0
    assert screener._context_ok(df, 29, {"rs_rank_min": 60}) is False
    assert screener._context_ok(df, 29, {"rs_rank_min": 20}) is True
    assert screener._context_ok(df, 29, {"rs_rank_min": None}) is True


def test_context_gate_degrades_when_columns_absent():
    """Without the pre-pass the screener must behave exactly as before."""
    df = _frame([10.0] * 30)
    assert screener._context_ok(df, 29, {"require_market_regime": True,
                                         "rs_rank_min": 90}) is True


def test_attach_all_can_be_disabled():
    u = _universe(n_symbols=5, n_bars=100)
    saved = relative.RS
    try:
        relative.RS = {"enabled": False}
        relative.attach_all(u)
        assert "rs_rank" not in u["S0"].columns
    finally:
        relative.RS = saved
