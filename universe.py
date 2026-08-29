"""Discover the current Bursa equity universe through TradingView's scanner."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import requests

URL = "https://scanner.tradingview.com/malaysia/scan"
CACHE = Path(".cache/bursa_universe.csv")


def _request() -> pd.DataFrame:
    payload = {
        "filter": [
            {"left": "exchange", "operation": "equal", "right": "MYX"},
            {"left": "type", "operation": "equal", "right": "stock"},
        ],
        "options": {"lang": "en"},
        "markets": ["malaysia"],
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": ["name", "description", "sector", "type", "subtype", "exchange"],
        "sort": {"sortBy": "Value.Traded", "sortOrder": "desc"},
        "range": [0, 3000],
    }
    response = requests.post(URL, json=payload, timeout=45)
    response.raise_for_status()
    rows = []
    for item in response.json().get("data", []):
        values = item.get("d") or []
        if len(values) < 6:
            continue
        code = str(values[0] or "").strip().upper()
        if not code:
            continue
        qualified = str(item.get("s") or f"MYX:{code}").strip().upper()
        tv_exchange, _, tv_code = qualified.partition(":")
        rows.append({
            "symbol": f"{code}.KL",
            "code": code,
            "tv_symbol": qualified,
            "tv_exchange": tv_exchange or "MYX",
            "tv_code": tv_code or code,
            "yahoo_symbol": f"{code}.KL",
            "description": str(values[1] or code),
            "sector": str(values[2] or "Unclassified"),
            "type": str(values[3] or "stock"),
            "subtype": str(values[4] or ""),
            "exchange": str(values[5] or "MYX"),
        })
    frame = pd.DataFrame(rows).drop_duplicates("symbol")
    if len(frame) < 800:
        raise RuntimeError(f"TradingView returned only {len(frame)} Bursa equities")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(CACHE, index=False)
    return frame


@lru_cache(maxsize=1)
def get_universe() -> pd.DataFrame:
    """Return one consistent universe snapshot for the lifetime of a run."""
    try:
        return _request()
    except Exception:
        if CACHE.exists():
            cached = pd.read_csv(CACHE)
            if len(cached) >= 800:
                return cached
        raise
