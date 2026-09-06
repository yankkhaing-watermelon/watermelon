"""Walk-forward backtest of the restored absolute-TA engine -> /backtest JSON.

Signals come from the SAME per-bar ``screener.CHECKS`` the live scan uses, so
backtest and live cannot drift. Each signal is entered at the next session's
open and run under BOTH exit policies (see exits.py):

    Fixed        +15% target / -7% stop / 20-day hold        ("fixed_15_7")
    ATR trail 3x  no target / -7% initial / 3xATR14 trail / 250d  ("chandelier_3atr")

For each strategy the policy with the higher *train* profit factor is published
as the default, with both kept so the app's two buttons work. A 70/30
chronological train/test split (config.BACKTEST) makes the test half genuinely
out-of-sample -- which it only stays if no choice is made on it, so the exit
rule is picked on train and merely reported on test.

Positions still open when the price history ends (exit reason "eod") are dropped
rather than marked to the final close. Marking them in flatters whichever rule
holds longest: the 250-session chandelier accumulates unrealised winners at the
right edge of the sample while the 20-day fixed rule cannot.

CI tractability: the universe is capped to the most liquid names and signals are
evaluated on a session stride; both are env-tunable.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import config
import data_fetcher
import relative
import screener
from indicators import enrich
from exits import POLICIES, POLICY_LABELS

OUT = Path(os.environ.get("PUBLIC_DIR", "public"))

BT = config.BACKTEST
SPLIT = float(os.environ.get("BT_SPLIT", str(BT.get("train_test_split", 0.70))))
COMMISSION_PCT = float(BT.get("commission_pct", 0.15))
LOOKBACK_YEARS = float(os.environ.get("BT_LOOKBACK_YEARS", "3.0"))
STEP_DAYS = int(os.environ.get("BT_STEP_DAYS", "3"))
MAX_SYMBOLS = int(os.environ.get("BT_MAX_SYMBOLS", "300"))
MIN_HISTORY = int(os.environ.get("BT_MIN_HISTORY", "260"))
EQUITY_POINTS = 60
MAX_TEST_TRADES_PUBLISHED = 250

STRATEGY_LABELS = {
    "trending": "Trending", "early_uptrend": "Early Uptrend",
    "reversal": "Confirmed Reversal", "gaining_momentum": "Gaining Momentum",
    "base_breakout": "Base Breakout", "meta_leader": "M.E.T.A. Technical Leader",
}
STRATEGIES = tuple(STRATEGY_LABELS)


@dataclass
class Trade:
    strategy: str
    policy: str
    symbol: str
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    ret_pct: float
    hold_days: int
    phase: str
    reason: str
    rs: float | None = None


def _rs_at(frame, i):
    """rs_rank on the signal bar, so decile performance can be checked later."""
    if "rs_rank" not in frame.columns:
        return None
    return _round(frame["rs_rank"].iloc[i], 1)


def _round(value, digits=2):
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _liquidity_rank(prices):
    scored = []
    for symbol, frame in prices.items():
        try:
            tv = (frame["close"] * frame["volume"]).tail(20)
            med = float(tv.median()) if len(tv) else 0.0
        except Exception:
            med = 0.0
        if math.isfinite(med) and med > 0:
            scored.append((med, symbol))
    scored.sort(reverse=True)
    return [s for _, s in scored[:MAX_SYMBOLS]]


def _run(prices, metadata):
    # Enrich once per symbol; the old checks read this exact frame.
    enriched = {}
    for sym, raw in prices.items():
        try:
            e = enrich(raw)
        except Exception:
            continue
        if len(e) >= MIN_HISTORY:
            enriched[sym] = e
    if not enriched:
        raise RuntimeError("no symbols with sufficient history")

    # Cross-sectional pre-pass. Must run on the full enriched set before any
    # check, and must be the same call the live scan makes, or the rs_rank and
    # regime_ok the backtest measures are not the ones live trading will see.
    relative.attach_all(enriched)

    # Common calendar and window from the union of enriched indices.
    all_dates = sorted(set().union(*[set(e.index) for e in enriched.values()]))
    window = int(LOOKBACK_YEARS * 252)
    start_i = max(MIN_HISTORY, len(all_dates) - window)
    scan_dates = all_dates[start_i:-1:STEP_DAYS]  # leave a tail for entry
    if not scan_dates:
        raise RuntimeError("insufficient history for a backtest window")
    split_date = scan_dates[max(0, int(len(scan_dates) * SPLIT) - 1)]

    strat_params = {s: config.STRATEGIES[s] for s in STRATEGIES if s in config.STRATEGIES}
    commission = COMMISSION_PCT * 2.0
    # One open position per (symbol, strategy, policy).
    blocked = {pol: {} for pol in POLICIES}
    trades: list[Trade] = []

    for as_of in scan_dates:
        for sym, e in enriched.items():
            pos = e.index.get_indexer([as_of])
            i = int(pos[0])
            if i < MIN_HISTORY - 1:
                continue
            future = e.iloc[i + 1:]
            if len(future) < 2:
                continue
            entry_date = future.index[0]
            entry_price = float(future["open"].iloc[0])
            if not math.isfinite(entry_price) or entry_price <= 0:
                continue
            for strategy, params in strat_params.items():
                check = screener.CHECKS.get(strategy)
                if check is None:
                    continue
                try:
                    if not check(e, i, params):
                        continue
                except Exception:
                    continue
                phase = "train" if entry_date <= split_date else "test"
                for pol_name, policy in POLICIES.items():
                    key = (sym, strategy)
                    if key in blocked[pol_name] and as_of <= blocked[pol_name][key]:
                        continue
                    res = policy(future.iloc[1:], entry_price)
                    trades.append(Trade(
                        strategy=strategy, policy=pol_name, symbol=sym.replace(".KL", ""),
                        entry_date=entry_date, exit_date=res.exit_date,
                        ret_pct=res.gross_ret_pct - commission,
                        hold_days=res.hold_sessions, phase=phase,
                        reason=res.reason, rs=_rs_at(e, i)))
                    blocked[pol_name][key] = res.exit_date

    return trades, scan_dates[0], scan_dates[-1]


def _closed(trades):
    """Drop right-censored trades.

    ``exits._last`` returns reason ``"eod"`` when a position was still open when
    the price history ran out; the caller then marks it to the final close. Those
    are unrealised, and they bias any rule that holds long enough to accumulate
    them — the 250-session chandelier collects them, the 20-day fixed rule does
    not, so scoring the two against each other without censoring is not a fair
    comparison. A trail that genuinely triggers on the last bar has reason
    "trail" and is kept.
    """
    return [t for t in trades if t.reason != "eod"]


def _phase_stats(trades):
    if not trades:
        return {"trades": 0, "win_rate": None, "loss_rate": None, "won": 0, "lost": 0,
                "avg": None, "median": None, "profit_factor": None,
                "best": None, "worst": None, "avg_hold": None}
    rets = [t.ret_pct for t in trades]
    wins = [r for r in rets if r > 0]
    gains = sum(wins); losses = -sum(r for r in rets if r <= 0)
    pf = gains / losses if losses > 0 else (99.0 if gains > 0 else 0.0)
    # Share of gross profit contributed by the five best trades. Above ~50% the
    # "edge" is a handful of names, not a repeatable process.
    top5 = sum(sorted(wins, reverse=True)[:5])
    top5_share = 100 * top5 / gains if gains > 0 else None
    return {
        "trades": len(rets),
        "win_rate": _round(100 * len(wins) / len(rets), 1),
        "loss_rate": _round(100 * (len(rets) - len(wins)) / len(rets), 1),
        "won": len(wins), "lost": len(rets) - len(wins),
        "avg": _round(statistics.fmean(rets)),
        "median": _round(statistics.median(rets)),
        "profit_factor": _round(pf),
        "top5_profit_share": _round(top5_share, 1),
        "best": _round(max(rets)), "worst": _round(min(rets)),
        "avg_hold": _round(statistics.fmean(t.hold_days for t in trades), 1),
    }


def _equity(trades):
    ordered = sorted(trades, key=lambda t: t.exit_date)
    v = 100.0; curve = []
    for t in ordered:
        v *= 1 + t.ret_pct / 100.0
        curve.append({"d": t.exit_date.date().isoformat(), "v": round(v, 2)})
    if len(curve) <= EQUITY_POINTS:
        return curve
    step = len(curve) / EQUITY_POINTS
    out = [curve[min(len(curve) - 1, int(i * step))] for i in range(EQUITY_POINTS)]
    out[-1] = curve[-1]
    return out


def _mdd(curve):
    if not curve:
        return None
    peak = curve[0]["v"]; worst = 0.0
    for p in curve:
        peak = max(peak, p["v"]); worst = min(worst, p["v"] / peak - 1)
    return _round(worst * 100)


def _verdict(train, test, other_test_pf):
    """Grade a strategy on its out-of-sample half.

    Order matters: sample size, then whether the typical trade makes money, then
    profit factor, then decay against train. A positive mean with a negative
    median means a few outliers are carrying the rule, so that is checked before
    profit factor rather than after.
    """
    n = test["trades"] or 0
    if n < 30:
        return {"level": "thin", "text": f"Only {n} closed out-of-sample trades. Not enough to judge."}

    med = test["median"]
    tpf = test["profit_factor"] or 0.0
    trpf = train["profit_factor"] or 0.0
    share = test["top5_profit_share"]

    if tpf < 1.0:
        return {"level": "bad", "text": "Loses money out of sample. Entry criteria are the suspect."}
    if med is not None and med <= 0:
        return {"level": "bad", "text": (
            f"Median trade is {med}% — most trades lose. The positive average is "
            "outliers, not an edge you can size into.")}

    warn = ""
    if share is not None and share >= 50:
        warn = f" Top 5 trades are {share}% of gross profit — concentrated."
    cmp_ = ""
    if other_test_pf:
        cmp_ = f" Other exit scores {other_test_pf:.2f} on test."

    if tpf >= 1.5 and tpf >= 0.75 * trpf and not warn:
        return {"level": "good", "text": f"Test holds up against train — no sign of curve-fitting.{cmp_}"}
    if tpf >= 1.5 and tpf >= 0.75 * trpf:
        return {"level": "warn", "text": f"Test profit factor is fine but fragile.{warn}{cmp_}"}
    return {"level": "warn", "text": f"Test decays from train. Edge is real but weaker out of sample.{warn}{cmp_}"}


def _policy_payload(strategy, pol, trades):
    closed = _closed(trades)
    open_at_end = len(trades) - len(closed)
    tr = [t for t in closed if t.phase == "train"]
    te = [t for t in closed if t.phase == "test"]
    ts, tt = _phase_stats(tr), _phase_stats(te)
    curve_te = _equity(te)
    pub = sorted(te, key=lambda t: t.exit_date, reverse=True)[:MAX_TEST_TRADES_PUBLISHED]
    rows = [{"p": "test", "s": t.symbol, "in": t.entry_date.date().isoformat(),
             "out": t.exit_date.date().isoformat(), "r": _round(t.ret_pct),
             "rs": t.rs} for t in pub]
    return {"train": ts, "test": tt, "equity": {"train": _equity(tr), "test": curve_te},
            "max_drawdown": _mdd(curve_te), "trades_total": len(closed),
            "open_at_end": open_at_end, "trades": rows}


def _strategy_block(strategy, trades):
    per_pol = {pol: [t for t in trades if t.policy == pol] for pol in POLICIES}
    payloads = {pol: _policy_payload(strategy, pol, per_pol[pol]) for pol in POLICIES}
    # Pick the exit rule on TRAIN only. Selecting it on the test set — as this
    # did previously — spends the out-of-sample data on the choice, so the
    # published test profit factor is no longer out of sample.
    def train_pf(pol):
        v = payloads[pol]["train"]["profit_factor"]
        if v is None or payloads[pol]["train"]["trades"] < 20:
            return -1
        return v
    winner = max(POLICIES, key=train_pf)
    loser = next((p for p in POLICIES if p != winner), winner)
    win = payloads[winner]
    block = {"strategy": strategy, **win,
             "verdict": _verdict(win["train"], win["test"], payloads[loser]["test"]["profit_factor"]),
             "chosen_policy": winner, "chosen_policy_label": POLICY_LABELS[winner],
             "policies": {pol: {"label": POLICY_LABELS[pol],
                                "train": payloads[pol]["train"], "test": payloads[pol]["test"],
                                "equity": payloads[pol]["equity"],
                                "max_drawdown": payloads[pol]["max_drawdown"],
                                "trades_total": payloads[pol]["trades_total"],
                                "trades": payloads[pol]["trades"]} for pol in POLICIES}}
    return block


def _publish(names):
    base = os.environ.get("WORKER_URL", "").rstrip("/")
    token = os.environ.get("PUBLISH_TOKEN", "")
    if not base or not token:
        raise RuntimeError("WORKER_URL and PUBLISH_TOKEN required for --publish")
    import requests
    for name in names:
        path = OUT / f"{name}.json"
        if path.exists():
            r = requests.post(f"{base}/publish?key={name}", data=path.read_bytes(),
                              headers={"Content-Type": "application/json", "X-Publish-Token": token}, timeout=60)
            r.raise_for_status(); print(f"publish {name}: {r.status_code}")


def run(do_publish=False):
    prices_all = data_fetcher.fetch_market()
    keep = set(_liquidity_rank(prices_all))
    prices = {s: f for s, f in prices_all.items() if s in keep}
    trades, first_date, last_date = _run(prices, {})
    by_strategy = {s: [] for s in STRATEGIES}
    for t in trades:
        by_strategy.setdefault(t.strategy, []).append(t)

    strategies = [_strategy_block(s, by_strategy.get(s, [])) for s in STRATEGIES]
    now = datetime.now(timezone.utc)
    payload = {
        "generated_at": now.isoformat(), "market": config.MARKET, "currency": config.CURRENCY,
        "universe_size": len(prices), "split": SPLIT,
        "date_from": first_date.date().isoformat(), "date_to": last_date.date().isoformat(),
        "params": {"take_profit_pct": 15.0, "stop_loss_pct": -7.0, "hold_days": 20,
                   "atr_trail_mult": 3.0, "atr_initial_stop_pct": -7.0, "atr_hold_days": 250,
                   "commission_pct": COMMISSION_PCT},
        "exit_policies": [{"key": p, "label": POLICY_LABELS[p]} for p in POLICIES],
        "strategy_config": {s: config.STRATEGIES.get(s, {}) for s in STRATEGIES},
        "strategies": strategies,
        "equity_basis": "sequential_compounded_single_position",
        "note": ("Exit rule chosen on the training half only. Positions still open when "
                 "the price history ends are excluded rather than marked to the last "
                 "close. Entry at next bar's open; stops fill at the stop price, so real "
                 "fills on thin counters are worse. The equity curve compounds trades in "
                 "exit order and is not a portfolio curve — treat its drawdown as "
                 "indicative only. Not advice."),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "backtest.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    if do_publish:
        _publish(("backtest",))
    print(f"Backtest: {len(trades)} trades ({', '.join(POLICIES)}) across {len(prices)} names, "
          f"{first_date.date()} -> {last_date.date()}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    run(parser.parse_args().publish)
