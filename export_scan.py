"""Run the embedded V5.1 Balanced engine and publish PWA-compatible JSON."""
from __future__ import annotations

import argparse
import json
import math
import os
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import yfinance as yf

import data_fetcher
from bmk_screener_v3 import STRATEGIES, STRATEGY_LABELS, ScreenerConfig, enrich, screen_universe
from signal_log import update
from universe import get_universe

OUT = Path(os.environ.get("PUBLIC_DIR", "public"))
BENCHMARK_SYMBOL = os.environ.get("BENCHMARK_SYMBOL", "^KLSE")
DETAIL_BARS = int(os.environ.get("DETAIL_BARS", "130"))


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _normalise_frame(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return pd.DataFrame()
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = [str(col[0]) for col in out.columns]
    out.columns = [str(col).strip().lower().replace(" ", "_") for col in out.columns]
    if "adj_close" in out and "close" not in out:
        out = out.rename(columns={"adj_close": "close"})
    required = ["open", "high", "low", "close", "volume"]
    if not set(required).issubset(out.columns):
        return pd.DataFrame()
    out = out[required].apply(pd.to_numeric, errors="coerce")
    out.index = pd.to_datetime(out.index, errors="coerce")
    return out.loc[~out.index.isna()].sort_index().dropna(subset=["open", "high", "low", "close"])


def _num(value: Any, digits: int = 3) -> float | None:
    result = _finite(value, float("nan"))
    return round(result, digits) if math.isfinite(result) else None


def _series(frame: pd.DataFrame, bars: int) -> dict[str, list]:
    e = enrich(frame).tail(bars)
    out = {
        "t": [pd.Timestamp(i).strftime("%Y-%m-%d") for i in e.index],
        "o": [_num(v) for v in e["open"]], "h": [_num(v) for v in e["high"]],
        "l": [_num(v) for v in e["low"]], "c": [_num(v) for v in e["close"]],
        "v": [int(_finite(v)) for v in e["volume"]],
        "e20": [_num(v) for v in e["ema20"]],
        "e50": [_num(v) for v in e["sma50"]],
        "e200": [_num(v) for v in e["sma200"]],
        "vavg": [_num(v, 0) for v in e["volume_mean20"]],
    }
    return out


def _fetch_benchmark() -> pd.DataFrame:
    raw = yf.download(BENCHMARK_SYMBOL, period="2y", interval="1d", auto_adjust=True,
                      progress=False, threads=False, timeout=30)
    frame = _normalise_frame(raw)
    if len(frame) < 275:
        raise RuntimeError(f"Benchmark {BENCHMARK_SYMBOL} returned only {len(frame)} usable bars")
    return frame


def _metadata() -> dict[str, dict[str, str]]:
    table = get_universe()
    return {
        str(row.symbol): {"name": str(row.description), "sector": str(row.sector)}
        for row in table.itertuples(index=False)
    }


def _score_components(candidate: dict[str, Any]) -> dict[str, int]:
    metrics = candidate.get("metrics") or {}
    score = _finite(candidate.get("score"))
    return {
        "trend": round(_finite(metrics.get("trend"), score)),
        "momentum": round((_finite(metrics.get("rs126_percentile"), 50) + _finite(metrics.get("rs20_percentile"), 50)) / 2),
        "volume": round(min(100, 35 + _finite(metrics.get("volume_ratio20"), 1) * 35)),
        "quality": round(_finite(metrics.get("base_quality"), score)),
        "liquidity": round(_finite(metrics.get("liquidity_percentile"), 50)),
        "risk": round(max(0, min(100, 100 - _finite(candidate.get("risk_pct"), 10) * 6))),
    }


def _browser_rows(candidates: list[dict[str, Any]], prices: dict[str, pd.DataFrame], episodes: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for candidate in candidates:
        grouped[str(candidate["symbol"])].append(candidate)
    episode_index = {(str(r["symbol"]), str(r["strategy"])): r for r in episodes}
    today = date.today()
    output = []
    for symbol, group in grouped.items():
        group.sort(key=lambda x: (-_finite(x.get("priority_score")), -_finite(x.get("score"))))
        best = group[0]
        strategies = list(dict.fromkeys(str(x["strategy"]) for x in group))
        episode_rows = [episode_index.get((symbol, strategy), {}) for strategy in strategies]
        first_seen = min((str(r.get("first_seen")) for r in episode_rows if r.get("first_seen")), default=today.isoformat())
        clean = prices[symbol]
        previous = _finite(clean["close"].iloc[-2]) if len(clean) > 1 else 0
        change = (_finite(clean["close"].iloc[-1]) / previous - 1) * 100 if previous else 0
        metrics = best.get("metrics") or {}
        output.append({
            "symbol": symbol.removesuffix(".KL"), "yahoo_symbol": symbol,
            "name": best.get("name") or symbol.removesuffix(".KL"),
            "sector": best.get("sector") or "Unclassified",
            "strategy": best["strategy"], "strategies": strategies,
            "strategy_label": best.get("strategy_label") or STRATEGY_LABELS.get(best["strategy"]),
            "score": best.get("score"), "priority_score": best.get("priority_score"),
            "score_components": _score_components(best), "reasons": best.get("reasons", []),
            "failed": best.get("warnings", []), "price": best.get("price"), "close": best.get("price"),
            "entry_low": best.get("entry_low"), "entry_high": best.get("entry_high"),
            "entry": best.get("entry_low"), "pivot": best.get("pivot"), "stop": best.get("suggested_stop"),
            "risk_pct": best.get("risk_pct"), "max_order_value_5pct_adv": best.get("max_order_value_5pct_adv"),
            "market_regime": best.get("market_regime"), "metrics": metrics,
            "rsi": metrics.get("rsi14"), "adx": metrics.get("adx14"),
            "vol_ratio": metrics.get("volume_ratio20"), "roc10": metrics.get("residual_momentum_20"),
            "change_pct": round(change, 2), "confluence": len(strategies),
            "is_new": first_seen == today.isoformat(), "first_seen": first_seen,
            "appearances": max((int(r.get("appearances") or 1) for r in episode_rows), default=1),
            "age_days": max(1, (today - date.fromisoformat(first_seen)).days + 1),
            "status": str(best.get("status") or "ACTIVE").upper(),
            "spark": _series(clean, 20),
        })
    return sorted(output, key=lambda x: (-_finite(x.get("priority_score"), _finite(x.get("score"))), x["symbol"]))


def publish_files(names: tuple[str, ...] = ("latest", "history"), only: tuple[str, ...] | None = None) -> None:
    if only is not None:
        names = only
    base = os.environ.get("WORKER_URL", "").rstrip("/")
    token = os.environ.get("PUBLISH_TOKEN", "")
    if not base or not token:
        raise RuntimeError("WORKER_URL and PUBLISH_TOKEN are required for --publish")
    for name in names:
        path = OUT / f"{name}.json"
        if not path.exists():
            continue
        response = requests.post(f"{base}/publish?key={name}", data=path.read_bytes(),
                                 headers={"Content-Type": "application/json", "X-Publish-Token": token}, timeout=60)
        response.raise_for_status()
        print(f"publish {name}: {response.status_code}")


def run(do_publish: bool = False) -> dict[str, Any]:
    prices = data_fetcher.fetch_market()
    if len(prices) < int(os.environ.get("MIN_UNIVERSE", "800")):
        raise RuntimeError(f"Fail closed: only {len(prices)} Bursa symbols returned usable history")
    diagnostics: dict[str, Any] = {}
    candidates = screen_universe(prices, _fetch_benchmark(), _metadata(), ScreenerConfig(), diagnostics=diagnostics)
    new_count, episodes = update([{"symbol": c["symbol"], "strategy": c["strategy"], "price": c.get("price"), "score": c.get("score")} for c in candidates])
    stocks = _browser_rows(candidates, prices, episodes)
    history = {row["symbol"]: _series(prices[row["yahoo_symbol"]], DETAIL_BARS) for row in stocks}
    now = datetime.now(timezone.utc)
    latest = {
        "generated_at": now.isoformat(), "scan_date": now.astimezone().date().isoformat(),
        "engine": "Bursa MusangKing V5.1 Balanced", "version": "5.1-balanced",
        "benchmark": BENCHMARK_SYMBOL, "market": "MYX", "market_name": "Bursa Malaysia", "currency": "RM",
        "stocks_screened": len(prices), "candidate_rows": len(candidates), "total_hits": len(stocks), "new_hits": new_count,
        "strategies": [{"key": strategy, "label": STRATEGY_LABELS[strategy],
                        "count": sum(strategy in row["strategies"] for row in stocks)} for strategy in STRATEGIES],
        "stocks": stocks, "diagnostics": diagnostics,
        "disclaimer": "Candidate-ranking output only. WATCH means market confirmation is weak. Not financial advice.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latest.json").write_text(json.dumps(latest, separators=(",", ":")), encoding="utf-8")
    history_payload = {"generated_at": now.isoformat(), "bars": DETAIL_BARS, "series": history}
    (OUT / "history.json").write_text(json.dumps(history_payload, separators=(",", ":")), encoding="utf-8")
    if do_publish:
        publish_files()
    print(f"V5.1 exported {len(stocks)} unique candidates from {len(prices)} stocks")
    return latest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    run(parser.parse_args().publish)
