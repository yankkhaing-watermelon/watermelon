"""Batched Yahoo OHLCV downloader for the complete Bursa universe."""
from __future__ import annotations

import os
import time
from collections.abc import Iterable

import pandas as pd
import yfinance as yf

import config
from universe import get_universe

PERIOD = os.environ.get("PRICE_PERIOD", "2y")


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame()
    frame = raw.copy()
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    if "adj_close" in frame and "close" not in frame:
        frame = frame.rename(columns={"adj_close": "close"})
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(frame.columns):
        return pd.DataFrame()
    frame = frame[required].apply(pd.to_numeric, errors="coerce")
    index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    frame.index = index
    return frame.loc[~frame.index.isna()].sort_index().dropna(subset=["open", "high", "low", "close"])


def _split_download(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    if raw.empty:
        return output
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(map(str, raw.columns.get_level_values(0)))
        ticker_first = any(symbol in level0 for symbol in symbols)
        for symbol in symbols:
            try:
                part = raw[symbol] if ticker_first else raw.xs(symbol, axis=1, level=1)
            except (KeyError, ValueError):
                continue
            clean = _normalise(part)
            if len(clean) >= 275:
                output[symbol] = clean
    elif len(symbols) == 1:
        clean = _normalise(raw)
        if len(clean) >= 275:
            output[symbols[0]] = clean
    return output


def fetch_many(symbols: Iterable[str], max_workers: int | None = None) -> dict[str, pd.DataFrame]:
    del max_workers
    ordered = list(dict.fromkeys(str(s).strip().upper() for s in symbols if str(s).strip()))
    batch_size = max(20, int(config.UNIVERSE["batch_size"]))
    result: dict[str, pd.DataFrame] = {}
    for offset in range(0, len(ordered), batch_size):
        batch = ordered[offset: offset + batch_size]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                raw = yf.download(
                    tickers=batch, period=PERIOD, interval="1d", auto_adjust=True,
                    progress=False, group_by="ticker", threads=True, timeout=30,
                )
                result.update(_split_download(raw, batch))
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        if last_error:
            print(f"batch {offset // batch_size + 1} failed: {last_error}")
        print(f"market data {min(offset + len(batch), len(ordered))}/{len(ordered)}; usable={len(result)}")
    return result


def fetch_market() -> dict[str, pd.DataFrame]:
    return fetch_many(get_universe()["symbol"].dropna().tolist())


def fetch_watchlist() -> dict[str, pd.DataFrame]:
    return fetch_many(config.WATCHLIST)

