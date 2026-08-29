from __future__ import annotations

import pandas as pd

import data_fetcher as module


def _frame(seed: float) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=300)
    close = pd.Series(seed, index=index) + pd.Series(range(300), index=index) * 0.001
    return pd.DataFrame({"open": close, "high": close + 0.01, "low": close - 0.01,
                         "close": close, "volume": 1_000_000}, index=index)


def test_tradingview_is_primary_and_yahoo_only_receives_unresolved(monkeypatch):
    rows = [
        {"key": "AAA.KL", "tv_code": "AAA", "tv_exchange": "MYX", "yahoo_symbol": "AAA.KL"},
        {"key": "BBB.KL", "tv_code": "BBB", "tv_exchange": "MYX", "yahoo_symbol": "BBB.KL"},
    ]
    monkeypatch.setattr(module, "_universe_rows", lambda symbols=None: rows)
    monkeypatch.setattr(module, "_tradingview_primary", lambda items, workers: ({"AAA.KL": _frame(1)}, [rows[1]]))
    seen = []

    def fallback(items):
        seen.extend(items)
        return {"BBB.KL": _frame(2)}

    monkeypatch.setattr(module, "_yahoo_fallback", fallback)
    result = module.fetch_many(["AAA.KL", "BBB.KL"], max_workers=2)
    assert set(result) == {"AAA.KL", "BBB.KL"}
    assert [row["key"] for row in seen] == ["BBB.KL"]


def test_yahoo_is_not_called_when_tradingview_resolves_everything(monkeypatch):
    rows = [{"key": "AAA.KL", "tv_code": "AAA", "tv_exchange": "MYX", "yahoo_symbol": "AAA.KL"}]
    monkeypatch.setattr(module, "_universe_rows", lambda symbols=None: rows)
    monkeypatch.setattr(module, "_tradingview_primary", lambda items, workers: ({"AAA.KL": _frame(1)}, []))

    def should_not_run(items):
        raise AssertionError("Yahoo fallback was called without unresolved symbols")

    monkeypatch.setattr(module, "_yahoo_fallback", should_not_run)
    result = module.fetch_many(["AAA.KL"], max_workers=1)
    assert set(result) == {"AAA.KL"}

