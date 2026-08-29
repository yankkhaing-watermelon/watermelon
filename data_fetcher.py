"""TradingView-first OHLCV downloader with a throttled Yahoo fallback.

TradingView/tvDatafeed supplies daily history for the full Bursa universe.
Yahoo is contacted only for symbols that remain unresolved after TradingView.
"""
from __future__ import annotations

import os
import random
import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
import requests
import yfinance as yf

import config
from universe import get_universe

MIN_BARS = int(os.environ.get("MIN_PRICE_BARS", "275"))
TV_BARS = int(os.environ.get("TV_BARS", "300"))
YF_RETRY_WAIT = float(os.environ.get("YF_RETRY_WAIT", "20"))
TV_CONNECT_GAP = float(os.environ.get("TV_CONNECT_GAP", "0.25"))
_tv_connect_lock = threading.Lock()
_tv_next_connect = 0.0
_tv_thread = threading.local()


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame()
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [str(c[0]) for c in frame.columns]
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
    # Daily bars from tvDatafeed commonly carry an exchange time while Yahoo
    # daily bars use midnight.  The screener joins by trading session, not by
    # the vendor's timestamp representation.
    frame.index = index.normalize()
    frame = frame.loc[~frame.index.isna()].sort_index()
    frame = frame.loc[~frame.index.duplicated(keep="last")]
    return frame.dropna(
        subset=["open", "high", "low", "close"]
    )


def _new_tv_client() -> tuple[Any, Any]:
    try:
        from tvDatafeed import Interval, TvDatafeed
    except ImportError as exc:
        raise RuntimeError(
            "tvDatafeed is not installed; the workflow must install "
            "git+https://github.com/rongardF/tvdatafeed.git"
        ) from exc
    logging.getLogger("tvDatafeed.main").setLevel(logging.CRITICAL)
    username = os.environ.get("TV_USERNAME")
    password = os.environ.get("TV_PASSWORD")
    client = TvDatafeed(username, password) if username and password else TvDatafeed()
    return client, Interval


def _tv_client(reset: bool = False) -> tuple[Any, Any]:
    """Reuse one client per worker thread; recreate it only after a failure."""
    if reset or not hasattr(_tv_thread, "client"):
        _tv_thread.client, _tv_thread.interval = _new_tv_client()
    return _tv_thread.client, _tv_thread.interval


def _pace_tradingview_connection() -> None:
    global _tv_next_connect
    with _tv_connect_lock:
        now = time.monotonic()
        wait = _tv_next_connect - now
        if wait > 0:
            time.sleep(wait)
        _tv_next_connect = time.monotonic() + TV_CONNECT_GAP


def _fetch_tradingview_symbol(row: dict[str, str]) -> pd.DataFrame:
    last_error: Exception | None = None
    empty_responses = 0
    for attempt in range(4):
        try:
            _pace_tradingview_connection()
            # Keep the anonymous/login handshake per thread on the happy path.
            # A retry gets a fresh client and fresh session IDs.
            client, interval = _tv_client(reset=attempt > 0)
            raw = client.get_hist(
                symbol=row["tv_code"],
                exchange=row.get("tv_exchange") or "MYX",
                interval=interval.in_daily,
                n_bars=TV_BARS,
                extended_session=False,
            )
            clean = _normalise(raw)
            if len(clean) >= MIN_BARS:
                return clean
            empty_responses += 1
            last_error = RuntimeError(f"only {len(clean)} usable TradingView bars")
        except Exception as exc:
            last_error = exc
        # Two clean no-data responses usually mean a genuine unsupported or
        # newly-listed symbol. Move it to Yahoo without wasting four sockets.
        if empty_responses >= 2:
            break
        if attempt < 3:
            time.sleep(min(12.0, 1.5 * (2 ** attempt)) + random.random())
    if last_error:
        raise last_error
    return pd.DataFrame()


def _universe_rows(symbols: Iterable[str] | None = None) -> list[dict[str, str]]:
    wanted = None
    if symbols is not None:
        wanted = {str(s).strip().upper() for s in symbols if str(s).strip()}
    table = get_universe()
    rows: list[dict[str, str]] = []
    for item in table.to_dict("records"):
        code = str(item.get("code") or item.get("symbol") or "").removesuffix(".KL").upper()
        yahoo = str(item.get("yahoo_symbol") or item.get("symbol") or f"{code}.KL").upper()
        if wanted is not None and yahoo not in wanted and code not in wanted:
            continue
        qualified = str(item.get("tv_symbol") or f"MYX:{code}").upper()
        exchange, _, tv_code = qualified.partition(":")
        rows.append({
            "key": yahoo,
            "code": code,
            "name": str(item.get("description") or code),
            "tv_symbol": qualified,
            "tv_exchange": str(item.get("tv_exchange") or exchange or "MYX"),
            "tv_code": str(item.get("tv_code") or tv_code or code),
            "yahoo_symbol": yahoo,
        })
    if wanted is not None:
        known = {row["key"] for row in rows}
        for raw in sorted(wanted):
            yahoo = raw if raw.endswith(".KL") else f"{raw}.KL"
            if yahoo in known:
                continue
            code = yahoo.removesuffix(".KL")
            rows.append({"key": yahoo, "code": code, "name": code,
                         "tv_symbol": f"MYX:{code}", "tv_exchange": "MYX",
                         "tv_code": code, "yahoo_symbol": yahoo})
    return list({row["key"]: row for row in rows}.values())


def _tradingview_primary(rows: list[dict[str, str]], max_workers: int) -> tuple[dict[str, pd.DataFrame], list[dict[str, str]]]:
    result: dict[str, pd.DataFrame] = {}
    unresolved: list[dict[str, str]] = []
    completed = 0
    print(
        f"TradingView primary starting: {len(rows)} symbols, "
        f"workers={max(1, min(max_workers, 6))}, bars={TV_BARS}"
    )
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, 6))) as pool:
        futures = {pool.submit(_fetch_tradingview_symbol, row): row for row in rows}
        for future in as_completed(futures):
            row = futures[future]
            completed += 1
            try:
                frame = future.result()
                if len(frame) >= MIN_BARS:
                    result[row["key"]] = frame
                else:
                    unresolved.append(row)
            except Exception:
                unresolved.append(row)
            if completed % 50 == 0 or completed == len(rows):
                print(
                    f"TradingView primary {completed}/{len(rows)}; "
                    f"usable={len(result)} unresolved={len(unresolved)}"
                )
    return result, unresolved


def _split_yahoo(raw: pd.DataFrame, symbols: list[str]) -> dict[str, pd.DataFrame]:
    output: dict[str, pd.DataFrame] = {}
    if not isinstance(raw, pd.DataFrame) or raw.empty:
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
            if len(clean) >= MIN_BARS:
                output[symbol] = clean
    elif len(symbols) == 1:
        clean = _normalise(raw)
        if len(clean) >= MIN_BARS:
            output[symbols[0]] = clean
    return output


def _resolve_yahoo_symbol(row: dict[str, str], session: requests.Session) -> str:
    direct = row["yahoo_symbol"]
    try:
        response = session.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": row["code"], "quotesCount": 10, "newsCount": 0},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=15,
        )
        if response.ok:
            quotes = response.json().get("quotes", [])
            matches = [str(q.get("symbol") or "") for q in quotes]
            kl = next((symbol for symbol in matches if symbol.upper().endswith(".KL")), None)
            if kl:
                return kl.upper()
    except Exception:
        pass
    return direct


def _yahoo_fallback(rows: list[dict[str, str]]) -> dict[str, pd.DataFrame]:
    if not rows:
        return {}
    batch_size = max(1, min(int(config.UNIVERSE["batch_size"]), 20))
    resolved: dict[str, str] = {}
    session = requests.Session()
    for row in rows:
        resolved[row["key"]] = _resolve_yahoo_symbol(row, session)
        time.sleep(0.12)

    result: dict[str, pd.DataFrame] = {}
    pairs = list(resolved.items())
    for offset in range(0, len(pairs), batch_size):
        batch_pairs = pairs[offset: offset + batch_size]
        yahoo_symbols = list(dict.fromkeys(symbol for _, symbol in batch_pairs))
        downloaded: dict[str, pd.DataFrame] = {}
        for attempt in range(3):
            try:
                raw = yf.download(
                    tickers=yahoo_symbols, period="2y", interval="1d",
                    # TradingView is the primary source and supplies raw OHLC.
                    # Keep Yahoo fallback raw too so cross-sectional momentum
                    # never mixes adjusted and unadjusted price conventions.
                    auto_adjust=False, progress=False, group_by="ticker",
                    threads=False, timeout=30,
                )
                downloaded = _split_yahoo(raw, yahoo_symbols)
                if downloaded or attempt == 2:
                    break
            except Exception as exc:
                print(f"Yahoo fallback batch retry {attempt + 1}: {exc}")
            time.sleep(YF_RETRY_WAIT * (attempt + 1))
        for key, yahoo_symbol in batch_pairs:
            frame = downloaded.get(yahoo_symbol)
            if frame is not None and len(frame) >= MIN_BARS:
                result[key] = frame
        done = min(offset + len(batch_pairs), len(pairs))
        print(f"Yahoo fallback {done}/{len(pairs)}; recovered={len(result)}")
        if done < len(pairs):
            time.sleep(2.0 + random.random())
    return result


def fetch_many(symbols: Iterable[str], max_workers: int | None = None) -> dict[str, pd.DataFrame]:
    rows = _universe_rows(symbols)
    workers = max_workers or int(config.UNIVERSE["max_workers"])
    primary, unresolved = _tradingview_primary(rows, workers)
    print(
        f"TradingView primary complete: usable={len(primary)}/{len(rows)}; "
        f"Yahoo fallback required={len(unresolved)}"
    )
    fallback = _yahoo_fallback(unresolved) if unresolved else {}
    primary.update(fallback)
    print(
        f"Market data complete: usable={len(primary)}/{len(rows)} "
        f"(TradingView={len(primary) - len(fallback)}, Yahoo={len(fallback)})"
    )
    return primary


def fetch_market() -> dict[str, pd.DataFrame]:
    rows = _universe_rows()
    workers = int(config.UNIVERSE["max_workers"])
    primary, unresolved = _tradingview_primary(rows, workers)
    print(
        f"TradingView primary complete: usable={len(primary)}/{len(rows)}; "
        f"Yahoo fallback required={len(unresolved)}"
    )
    fallback = _yahoo_fallback(unresolved) if unresolved else {}
    primary.update(fallback)
    print(
        f"Market data complete: usable={len(primary)}/{len(rows)} "
        f"(TradingView={len(primary) - len(fallback)}, Yahoo={len(fallback)})"
    )
    return primary


def fetch_watchlist() -> dict[str, pd.DataFrame]:
    return fetch_many(config.WATCHLIST)
