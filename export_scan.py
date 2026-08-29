"""Run the restored absolute-TA six-strategy engine and publish app JSON.

This replaces the relative-strength exporter. Signals come from the byte-faithful
old ``screener.scan()`` (ADX/RSI/MACD/ROC/volume, per-symbol, no benchmark, no
regime overlay). Output matches the PWA contract the app already reads:
per-stock ``spark``, ``is_new`` (via signal_log episodes), a ``strategies``
array, and ``change_pct``; plus a ``/history`` series and worker publish.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

import config
import data_fetcher
import screener
from indicators import enrich
from signal_log import update
from universe import get_universe

OUT = Path(os.environ.get("PUBLIC_DIR", "public"))
DETAIL_BARS = int(os.environ.get("DETAIL_BARS", "130"))
SPARK_BARS = int(os.environ.get("SPARK_BARS", "20"))

STRATEGY_LABELS = {
    "trending": "Trending",
    "early_uptrend": "Early Uptrend",
    "reversal": "Confirmed Reversal",
    "gaining_momentum": "Gaining Momentum",
    "base_breakout": "Base Breakout",
    "meta_leader": "M.E.T.A. Technical Leader",
}
STRATEGIES = tuple(STRATEGY_LABELS)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _num(value: Any, digits: int = 3) -> float | None:
    result = _finite(value, float("nan"))
    return round(result, digits) if math.isfinite(result) else None


def _series(frame: pd.DataFrame, bars: int) -> dict[str, list]:
    e = enrich(frame).tail(bars)
    return {
        "t": [pd.Timestamp(i).strftime("%Y-%m-%d") for i in e.index],
        "o": [_num(v) for v in e["open"]], "h": [_num(v) for v in e["high"]],
        "l": [_num(v) for v in e["low"]], "c": [_num(v) for v in e["close"]],
        "v": [int(_finite(v)) for v in e["volume"]],
        "e20": [_num(v) for v in e["ema20"]],
        "e50": [_num(v) for v in e["ema50"]],
        "e200": [_num(v) for v in e["ema200"]],
    }


def _spark(frame: pd.DataFrame, bars: int) -> dict[str, list]:
    e = enrich(frame).tail(bars)
    return {
        "o": [_num(v) for v in e["open"]], "h": [_num(v) for v in e["high"]],
        "l": [_num(v) for v in e["low"]], "c": [_num(v) for v in e["close"]],
    }


def _metadata() -> dict[str, dict[str, str]]:
    table = get_universe()
    meta: dict[str, dict[str, str]] = {}
    for row in table.itertuples(index=False):
        code = str(getattr(row, "code", "") or getattr(row, "symbol", "")).replace(".KL", "").upper()
        meta[code] = {"name": str(getattr(row, "description", code)),
                      "sector": str(getattr(row, "sector", "Unclassified"))}
    return meta


def _change_pct(frame: pd.DataFrame) -> float:
    e = enrich(frame)
    if len(e) < 2:
        return 0.0
    prev, last = float(e["close"].iloc[-2]), float(e["close"].iloc[-1])
    return round((last / prev - 1) * 100.0, 2) if prev > 0 else 0.0


def _publish_files(names: tuple[str, ...]) -> None:
    base = os.environ.get("WORKER_URL", "").rstrip("/")
    token = os.environ.get("PUBLISH_TOKEN", "")
    if not base or not token:
        raise RuntimeError("WORKER_URL and PUBLISH_TOKEN are required for --publish")
    for name in names:
        path = OUT / f"{name}.json"
        if not path.exists():
            continue
        response = requests.post(f"{base}/publish?key={name}", data=path.read_bytes(),
                                 headers={"Content-Type": "application/json",
                                          "X-Publish-Token": token}, timeout=60)
        response.raise_for_status()
        print(f"publish {name}: {response.status_code}")


def publish_files(names: tuple[str, ...] = ("latest", "history"),
                  only: tuple[str, ...] | None = None) -> None:
    """Public wrapper. ``only`` overrides ``names`` (kept for export_review)."""
    _publish_files(only if only is not None else names)


def run(do_publish: bool = False) -> dict[str, Any]:
    prices = data_fetcher.fetch_market()
    if len(prices) < int(os.environ.get("MIN_UNIVERSE", "800")):
        raise RuntimeError(f"Fail closed: only {len(prices)} symbols returned usable history")

    by_code = {str(k).replace(".KL", "").upper(): v for k, v in prices.items()}
    hits = screener.scan(by_code)

    metadata = _metadata()
    per_symbol: dict[str, dict[str, Any]] = {}
    strat_of: dict[str, list[str]] = defaultdict(list)
    for strategy, rows in hits.items():
        for r in rows:
            sym = str(r["symbol"]).upper()
            strat_of[sym].append(strategy)
            if sym not in per_symbol:
                per_symbol[sym] = r

    signals = [{"symbol": s, "strategy": strat_of[s][0], "price": per_symbol[s].get("close"),
                "score": None} for s in per_symbol]
    new_count, episodes = update(signals)
    is_new = {str(e["symbol"]).upper(): bool(e.get("is_new")) for e in episodes}

    stocks: list[dict[str, Any]] = []
    for sym, r in per_symbol.items():
        frame = by_code.get(sym)
        if frame is None:
            continue
        meta = metadata.get(sym, {})
        stocks.append({
            "symbol": sym,
            "name": str(meta.get("name") or sym),
            "sector": str(meta.get("sector") or "Unclassified"),
            "strategy": strat_of[sym][0],
            "strategies": strat_of[sym],
            "status": "ACTIVE",
            "close": _num(r.get("close")),
            "price": _num(r.get("close")),
            "rsi": _num(r.get("rsi"), 1),
            "adx": _num(r.get("adx"), 1),
            "vol_ratio": _num(r.get("vol_ratio"), 2),
            "roc10": _num(r.get("roc10"), 2),
            "change_pct": _change_pct(frame),
            "is_new": is_new.get(sym, False),
            "spark": _spark(frame, SPARK_BARS),
        })

    stocks.sort(key=lambda s: -_finite(s.get("vol_ratio")))

    history = {s["symbol"]: _series(by_code[s["symbol"]], DETAIL_BARS) for s in stocks}
    now = datetime.now(timezone.utc)
    latest = {
        "generated_at": now.isoformat(), "scan_date": now.astimezone().date().isoformat(),
        "engine": "BursaMusangKing Absolute-TA (restored)", "version": "old-restore-1.0",
        "market": config.MARKET, "market_name": config.MARKET_NAME, "currency": config.CURRENCY,
        "stocks_screened": len(prices), "total_hits": len(stocks), "new_hits": new_count,
        "strategies": [{"key": s, "label": STRATEGY_LABELS[s],
                        "count": sum(s in row["strategies"] for row in stocks)} for s in STRATEGIES],
        "stocks": stocks,
        "disclaimer": "Absolute-TA candidate screen. Not financial advice.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(latest, separators=(",", ":")), encoding="utf-8")
    (OUT / "history.json").write_text(
        json.dumps({"generated_at": now.isoformat(), "bars": DETAIL_BARS, "series": history},
                   separators=(",", ":")), encoding="utf-8")
    if do_publish:
        _publish_files(("latest", "history"))
    print(f"Restored engine exported {len(stocks)} hits from {len(prices)} stocks")
    return latest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    run(parser.parse_args().publish)
