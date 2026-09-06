"""Tests for the null model.

The load-bearing test is that a genuinely random entry rule scores near the
50th percentile against its own null. If it did not, the benchmark would be
biased and every verdict built on it would be wrong.
"""
import random

import numpy as np
import pandas as pd
import pytest

import export_backtest as bt
import null_model as nm


def _frame(seed, n=400, drift=0.0005):
    rng = np.random.default_rng(seed)
    c = 10 * np.cumprod(1 + rng.normal(drift, 0.02, n))
    idx = pd.bdate_range("2023-01-01", periods=n)
    df = pd.DataFrame({"open": c * 0.998, "high": c * 1.02, "low": c * 0.98,
                       "close": c, "volume": rng.integers(1e6, 5e6, n)}, index=idx)
    df["atr14"] = (df["high"] - df["low"]).rolling(14, min_periods=1).mean()
    return df


@pytest.fixture(scope="module")
def universe():
    return {f"S{k}": _frame(k) for k in range(25)}


def test_stats_handles_empty_and_basic_cases():
    assert nm._stats([]) is None
    s = nm._stats([10.0, -5.0, -5.0, 20.0])
    assert s["trades"] == 4
    assert s["win_rate"] == 50.0
    assert s["profit_factor"] == 3.0


def test_percentile_ranks_correctly():
    sample = [1.0, 1.1, 1.2, 1.3, 1.4]
    assert nm._percentile(sample, 1.45) == 100.0
    assert nm._percentile(sample, 0.5) == 0.0
    assert nm._percentile(sample, 1.25) == 60.0
    assert nm._percentile(sample, None) is None


def test_simulate_drops_uncloseable_entries(universe):
    """An entry on the final bar has no future to trade and must be dropped."""
    last = universe["S0"].index[-1]
    out = nm._simulate(universe, list(universe), [("S0", last)], "fixed_15_7", 0.3)
    assert out == []


def test_simulate_applies_commission(universe):
    d = universe["S0"].index[300]
    gross = nm._simulate(universe, list(universe), [("S0", d)], "fixed_15_7", 0.0)
    net = nm._simulate(universe, list(universe), [("S0", d)], "fixed_15_7", 0.3)
    assert gross and net
    assert gross[0] - net[0] == pytest.approx(0.3)


def test_simulate_censors_open_positions(universe):
    """Nothing returned by _simulate may be an unclosed position."""
    dates = list(universe["S0"].index[280:320])
    entries = [("S0", d) for d in dates]
    from exits import POLICIES
    kept = nm._simulate(universe, list(universe), entries, "chandelier_3atr", 0.3)
    raw = []
    for _, d in [(s, d) for s, d in entries]:
        r = bt.entry_at(universe["S0"], d)
        if r:
            raw.append(POLICIES["chandelier_3atr"](r[1].iloc[1:], r[3]).reason)
    assert len(kept) == sum(1 for reason in raw if reason != "eod")


def test_random_entries_score_near_fiftieth_percentile(universe):
    """A random rule benchmarked against random must land mid-distribution.

    This is the calibration check on the benchmark itself. A drifting result
    would mean the null is systematically easier or harder than the thing it is
    meant to measure.
    """
    rng = random.Random(0)
    syms, dates = list(universe), list(universe["S0"].index[270:330])
    pcts = []
    for _ in range(15):
        actual = nm._stats(nm._simulate(universe, syms,
                                        nm._draw(rng, syms, dates, 60), "fixed_15_7", 0.3))
        null = [nm._stats(nm._simulate(universe, syms,
                                       nm._draw(rng, syms, dates, 60), "fixed_15_7", 0.3))
                for _ in range(30)]
        p = nm._percentile([n["profit_factor"] for n in null if n],
                           actual["profit_factor"])
        if p is not None:
            pcts.append(p)
    assert 25 <= sum(pcts) / len(pcts) <= 75


def test_prepare_and_entry_at_are_shared_with_the_real_backtest():
    """The null must not quietly diverge from the run it is benchmarking."""
    assert callable(bt.prepare)
    assert callable(bt.entry_at)
    assert nm.bt is bt
