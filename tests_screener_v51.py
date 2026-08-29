from __future__ import annotations

from pathlib import Path
import inspect
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import bmk_screener_v3 as m
from bmk_screener_v3 import ScreenerConfig, STRATEGIES, enrich, market_regime, screen_universe


def generic_frame(
    seed: int,
    days: int = 360,
    start: float = 1.0,
    drift: float = 0.0004,
    vol: float = 0.008,
    volume: float = 2_000_000,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=days)
    returns = drift + rng.normal(0, vol, days)
    close = start * np.cumprod(1 + returns)
    open_ = np.r_[close[0], close[:-1] * (1 + rng.normal(0, vol / 4, days - 1))]
    high = np.maximum(open_, close) * (1 + rng.uniform(0.002, max(0.003, vol), days))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.002, max(0.003, vol), days))
    shares = np.maximum(100_000, rng.normal(volume, volume * 0.10, days))
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": shares},
        index=idx,
    )


def accelerating_frame(
    seed: int,
    days: int = 360,
    start: float = 1.0,
    base: float = 0.0003,
    boost63: float = 0.0002,
    boost20: float = 0.0001,
    noise: float = 0.004,
    volume: float = 2_000_000,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-02", periods=days)
    returns = np.full(days, base) + rng.normal(0, noise, days)
    returns[-63:] += boost63
    returns[-20:] += boost20
    close = start * np.cumprod(1 + returns)
    open_ = np.r_[close[0], close[:-1] * (1 + rng.normal(0, noise / 5, days - 1))]
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    up = np.r_[False, np.diff(close) > 0]
    shares = np.where(up, volume * 1.5, volume * 0.7) * (1 + rng.normal(0, 0.05, days))
    shares[-1] = volume * 2.5
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": shares},
        index=idx,
    )


def contracted_base_frame() -> pd.DataFrame:
    days = 360
    idx = pd.bdate_range("2024-01-02", periods=days)
    rng = np.random.default_rng(99)
    close = np.empty(days)
    close[0] = 1.0
    for i in range(1, 319):
        close[i] = close[i - 1] * (1 + 0.0018 + rng.normal(0, 0.006))
    pivot = close[318] * 1.03
    for j, i in enumerate(range(319, 359)):
        amplitude = 0.07 * (1 - j / 55)
        centre = pivot * (0.94 + 0.02 * j / 40)
        close[i] = centre * (1 + rng.normal(0, amplitude / 5))
    close[359] = pivot * 1.015
    open_ = np.r_[close[0], close[:-1] * (1 + rng.normal(0, 0.002, days - 1))]
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    high[319:339] = np.minimum(high[319:339], pivot)
    low[319:339] = np.maximum(low[319:339], pivot * 0.89)
    high[320] = pivot
    high[339:359] = np.maximum(open_[339:359], close[339:359]) * 1.001
    low[339:359] = np.minimum(open_[339:359], close[339:359]) * 0.999
    shares = np.full(days, 2_000_000.0)
    shares[319:339] = 1_500_000
    shares[339:359] = 900_000
    shares[359] = 4_000_000
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": shares},
        index=idx,
    )


def meta_universe():
    benchmark = accelerating_frame(1, start=100, base=0.00025, boost63=0.00005, boost20=0.00005, noise=0.002, volume=20_000_000)
    prices = {
        f"W{i}.KL": accelerating_frame(
            10 + i,
            start=1 + i * 0.02,
            base=0.0002 + i * 0.000002,
            boost63=0.00005,
            boost20=0.00005,
            noise=0.005,
            volume=1_500_000,
        )
        for i in range(30)
    }
    prices["LEAD.KL"] = accelerating_frame(
        99,
        start=1.5,
        base=0.00055,
        boost63=0.0004,
        boost20=0.0015,
        noise=0.004,
        volume=4_000_000,
    )
    metadata = {
        symbol: {
            "sector": "LeaderSector"
            if symbol == "LEAD.KL"
            else ("SectorA" if int("".join(filter(str.isdigit, symbol)) or 0) < 10 else "SectorB")
        }
        for symbol in prices
    }
    return prices, benchmark, metadata


def base_universe(with_metadata: bool = True):
    benchmark = generic_frame(1, start=100, drift=0.0008, vol=0.003, volume=20_000_000)
    prices = {
        f"W{i}.KL": generic_frame(10 + i, start=1 + i * 0.05, drift=0.0002 + i * 0.00001, vol=0.009, volume=1_500_000)
        for i in range(20)
    }
    prices["BREAK.KL"] = contracted_base_frame()
    metadata = {symbol: {"sector": "LeaderSector" if symbol == "BREAK.KL" else "Other"} for symbol in prices} if with_metadata else {}
    return prices, benchmark, metadata


def test_indicators_are_deterministic_and_point_in_time():
    raw = generic_frame(5)
    a = enrich(raw)
    b = enrich(raw)
    pd.testing.assert_frame_equal(a, b)
    assert np.isfinite(a.iloc[-1]["atr14"])
    assert np.isfinite(a.iloc[-1]["sma200"])


def test_illiquid_stock_is_rejected():
    benchmark = generic_frame(1, start=100, drift=0.0007, vol=0.003, volume=20_000_000)
    liquid = generic_frame(2, start=2, drift=0.0012, vol=0.010, volume=2_000_000)
    illiquid = generic_frame(3, start=0.8, drift=0.0012, vol=0.010, volume=20_000)
    out = screen_universe({"GOOD.KL": liquid, "BAD.KL": illiquid}, benchmark, config=ScreenerConfig(min_score=0, min_meta_score=0))
    assert all(row["symbol"] != "BAD.KL" for row in out)


def test_risk_off_regime_marks_continuation_candidates_watch_only():
    benchmark = generic_frame(1, start=100, drift=-0.0010, vol=0.006, volume=20_000_000)
    prices = {f"S{i}.KL": generic_frame(20 + i, start=1 + i * 0.1, drift=0.0014, vol=0.010, volume=2_000_000) for i in range(20)}
    regime = market_regime(benchmark, prices)
    assert regime.state == "risk_off"
    out = screen_universe(prices, benchmark, config=ScreenerConfig(min_score=0, min_meta_score=0))
    continuation = {"trending", "early_uptrend", "gaining_momentum", "base_breakout", "meta_leader"}
    assert all(row["status"] == "WATCH" for row in out if row["strategy"] in continuation)


def test_meta_is_technical_only_and_passes_without_fundamentals():
    prices, benchmark, metadata = meta_universe()
    out = screen_universe(prices, benchmark, metadata)
    meta = [row for row in out if row["symbol"] == "LEAD.KL" and row["strategy"] == "meta_leader"]
    assert len(meta) == 1
    assert "Technical Leader" in meta[0]["strategy_label"]
    assert any("technical-only" in warning for warning in meta[0]["warnings"])
    assert meta[0]["score"] >= ScreenerConfig().min_meta_score


def test_base_breakout_requires_contraction_and_real_volume():
    prices, benchmark, metadata = base_universe()
    out = screen_universe(prices, benchmark, metadata)
    rows = [row for row in out if row["symbol"] == "BREAK.KL" and row["strategy"] == "base_breakout"]
    assert len(rows) == 1
    row = rows[0]
    assert row["pivot"] is not None
    assert row["metrics"]["base_quality"] >= 70
    assert row["metrics"]["volume_ratio20"] >= 1.5
    assert row["entry_low"] <= row["price"] <= row["entry_high"]


def test_missing_sector_labels_do_not_create_fake_sector_analysis_or_block_breakout():
    prices, benchmark, _ = base_universe(with_metadata=False)
    out = screen_universe(prices, benchmark)
    rows = [row for row in out if row["symbol"] == "BREAK.KL" and row["strategy"] == "base_breakout"]
    assert len(rows) == 1
    assert rows[0]["metrics"]["sector_percentile"] == 50.0


def test_as_of_result_matches_physically_sliced_data():
    prices, benchmark, metadata = meta_universe()
    as_of = benchmark.index[-25]
    full_asof = screen_universe(prices, benchmark, metadata, config=ScreenerConfig(min_score=0, min_meta_score=0), as_of=as_of)
    sliced_prices = {symbol: frame.loc[:as_of] for symbol, frame in prices.items()}
    sliced_benchmark = benchmark.loc[:as_of]
    sliced = screen_universe(sliced_prices, sliced_benchmark, metadata, config=ScreenerConfig(min_score=0, min_meta_score=0))
    assert full_asof == sliced



def test_diagnostics_explain_zero_or_low_result_counts():
    prices, benchmark, metadata = base_universe()
    diagnostics = {}
    out = screen_universe(prices, benchmark, metadata, diagnostics=diagnostics)
    assert diagnostics["version"] == "3.1-balanced"
    assert diagnostics["input_stocks"] == len(prices)
    assert diagnostics["feature_eligible"] > 0
    assert "tradability" in diagnostics
    assert set(diagnostics["strategies"]) == set(STRATEGIES)
    assert diagnostics["strategies"]["base_breakout"]["published"] == sum(
        row["strategy"] == "base_breakout" for row in out
    )


def test_output_is_deterministic():
    prices, benchmark, metadata = base_universe()
    a = screen_universe(prices, benchmark, metadata)
    b = screen_universe(prices, benchmark, metadata)
    assert a == b


def test_candidates_include_capacity_risk_and_confluence_fields():
    prices, benchmark, metadata = base_universe()
    out = screen_universe(prices, benchmark, metadata)
    row = next(item for item in out if item["symbol"] == "BREAK.KL")
    assert row["max_order_value_5pct_adv"] > 0
    assert 0 < row["risk_pct"] < 30
    assert row["confluence_count"] >= 1
    assert row["priority_score"] >= row["score"]


def test_source_contains_no_demo_or_random_fallback():
    source = inspect.getsource(m)
    forbidden = ("Math.random", "np.random", "makeDemo", "demoStocks", "synthetic fallback")
    assert all(token not in source for token in forbidden)


def test_all_six_strategies_are_exposed():
    assert STRATEGIES == (
        "trending",
        "early_uptrend",
        "reversal",
        "gaining_momentum",
        "base_breakout",
        "meta_leader",
    )
