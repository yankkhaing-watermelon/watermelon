"""
Weekly review -> JSON for the app's Weekly tab (+ optional Telegram).

Same question upstream review.py asks: how does a FRESH signal actually
perform? Only is_new signals are evaluated, at +5/+10/+20 trading days, so a
stock that trends for three weeks doesn't get counted fifteen times and
flatter the stats.

  python export_review.py
  SEND_TELEGRAM=1 python export_review.py --publish
"""

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import upstream
import signal_log as slog

OUT = Path("public")
HORIZONS = [5, 10, 20]
LOOKBACK_WEEKS = 8


def forward_returns(sig_date, sig_close: float, df: pd.DataFrame) -> dict:
    idx = df.index.searchsorted(pd.Timestamp(sig_date))
    if idx >= len(df):
        return {}
    out = {}
    for h in HORIZONS:
        j = idx + h
        if j < len(df):
            out[h] = (float(df["close"].iloc[j]) / sig_close - 1) * 100
    out["latest"] = (float(df["close"].iloc[-1]) / sig_close - 1) * 100
    return out


def _profit_factor(returns: list[float]) -> float | None:
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r < 0)
    if losses == 0:
        return None if gains == 0 else float("inf")
    return round(gains / losses, 2)


def run(publish: bool = False, send_telegram: bool | None = None) -> dict:
    eng = upstream.engine()
    config = eng["config"]
    data_fetcher = eng["data_fetcher"]

    if send_telegram is None:
        send_telegram = os.environ.get("SEND_TELEGRAM", "0") == "1"

    log = slog.load_log()
    now = datetime.now(timezone.utc)
    empty = {
        "generated_at": now.isoformat(),
        "lookback_weeks": LOOKBACK_WEEKS,
        "horizons": HORIZONS,
        "strategies": [],
        "overall": {},
        "equity_curve": [],
        "note": "Not enough signal history yet — needs a few more scan days.",
    }

    if log.empty:
        _write(empty, publish)
        print("no signals logged yet")
        return empty

    cutoff = pd.Timestamp(datetime.now().date()) - timedelta(weeks=LOOKBACK_WEEKS)
    log = log[(log["date"] >= cutoff) & (log["is_new"] == True)]  # noqa: E712
    if log.empty:
        _write(empty, publish)
        print("no new signals in review window")
        return empty

    symbols = sorted(log["symbol"].unique())
    print(f"reviewing {len(log)} new signals across {len(symbols)} symbols...")
    data = data_fetcher.fetch_many(symbols,
                                   max_workers=config.UNIVERSE["max_workers"])

    per_strategy, all_r10, trade_rows = [], [], []

    ALL_STRATEGIES = ["trending", "early_uptrend", "reversal",
                      "gaining_momentum", "base_breakout", "meta_leader"]
    for strat in ALL_STRATEGIES:
        s = log[log["strategy"] == strat]
        buckets = {h: [] for h in HORIZONS}
        open_rets, evaluated = [], 0
        best = {"symbol": None, "ret": None}
        worst = {"symbol": None, "ret": None}

        for _, row in s.iterrows():
            df = data.get(row["symbol"])
            if df is None or df.empty:
                continue
            fr = forward_returns(row["date"], float(row["close"]), df)
            if not fr:
                continue
            evaluated += 1
            got = False
            for h in HORIZONS:
                if h in fr:
                    buckets[h].append(fr[h])
                    got = True
            if not got:
                open_rets.append(fr["latest"])

            # Use the shortest COMPLETED horizon for the overall summary, so the
            # top cards populate as soon as 5d returns exist and stay consistent
            # with the per-strategy blocks (which also key off completed horizons).
            completed = [h for h in HORIZONS if h in fr]
            r_summary = fr[completed[0]] if completed else None
            if r_summary is not None:
                all_r10.append(r_summary)
                trade_rows.append({"date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
                                   "ret": r_summary})
                if best["ret"] is None or r_summary > best["ret"]:
                    best = {"symbol": row["symbol"], "ret": round(r_summary, 1)}
                if worst["ret"] is None or r_summary < worst["ret"]:
                    worst = {"symbol": row["symbol"], "ret": round(r_summary, 1)}

        # Always emit a block, even with no evaluable signals yet, so all six
        # strategies are visible. total_logged counts signals awaiting history.
        total_logged = int(len(s))

        horizons = {}
        for h in HORIZONS:
            if buckets[h]:
                arr = pd.Series(buckets[h])
                horizons[str(h)] = {
                    "n": int(len(arr)),
                    "win_rate": round(100 * float((arr > 0).mean()), 1),
                    "avg": round(float(arr.mean()), 2),
                    "median": round(float(arr.median()), 2),
                    "profit_factor": _profit_factor(list(arr)),
                }

        per_strategy.append({
            "strategy": strat,
            "signals": evaluated,
            "signals_logged": total_logged,
            "pending": int(max(0, total_logged - evaluated)),
            "horizons": horizons,
            "open": {"n": len(open_rets),
                     "avg": round(float(pd.Series(open_rets).mean()), 2)} if open_rets else None,
            "best": best if best["symbol"] else None,
            "worst": worst if worst["symbol"] else None,
        })

    overall = {}
    if all_r10:
        arr = pd.Series(all_r10)
        overall = {
            "trades": int(len(arr)),
            "win_rate": round(100 * float((arr > 0).mean()), 1),
            "avg": round(float(arr.mean()), 2),
            "profit_factor": _profit_factor(list(arr)),
            "worst": round(float(arr.min()), 2),
            "best": round(float(arr.max()), 2),
        }

    # cumulative average return by signal date — the Weekly tab's curve
    equity = []
    if trade_rows:
        td = pd.DataFrame(trade_rows).sort_values("date")
        running = 100.0
        for d, grp in td.groupby("date"):
            running *= (1 + float(grp["ret"].mean()) / 100 / len(HORIZONS))
            equity.append({"date": d, "value": round(running, 2)})

    report = {
        "generated_at": now.isoformat(),
        "market": config.MARKET,
        "lookback_weeks": LOOKBACK_WEEKS,
        "horizons": HORIZONS,
        "strategies": per_strategy,
        "overall": overall,
        "equity_curve": equity,
        "note": "Live signal performance. Past results do not predict future "
                "results — this is information, not financial advice.",
    }

    _write(report, publish)

    if send_telegram and per_strategy:
        _send_telegram(eng, report)

    print(f"weekly review: {overall.get('trades', 0)} evaluated trades")
    return report


def _write(report: dict, publish: bool):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "weekly.json").write_text(json.dumps(report, separators=(",", ":")))
    print("wrote public/weekly.json")
    if publish:
        from export_scan import publish_files
        publish_files(only=("weekly",))


def _send_telegram(eng, report: dict):
    tb = eng["telegram_bot"]
    labels = getattr(tb, "STRATEGY_LABELS", {})
    lines = [f"<b>Weekly signal review — {datetime.now():%d %b %Y}</b>",
             f"<i>New signals, last {LOOKBACK_WEEKS} weeks</i>", ""]
    o = report.get("overall") or {}
    if o:
        lines.append(f"Overall: {o['win_rate']}% win | avg {o['avg']:+.1f}% "
                     f"| PF {o['profit_factor']} | n={o['trades']}")
        lines.append("")
    for s in report["strategies"]:
        label = labels.get(s["strategy"], s["strategy"]).split("(")[0].strip()
        lines.append(f"<b>{label}</b> — {s['signals']} new signals")
        for h in HORIZONS:
            hh = s["horizons"].get(str(h))
            if hh:
                lines.append(f"  +{h}d: {hh['win_rate']}% win | "
                             f"avg {hh['avg']:+.1f}% (n={hh['n']})")
        lines.append(f"  best {s['best']['symbol']} {s['best']['ret']:+.1f}% | "
                     f"worst {s['worst']['symbol']} {s['worst']['ret']:+.1f}%")
        lines.append("")
    msg = "\n".join(lines)
    while msg:
        chunk, msg = msg[:4000], msg[4000:]
        tb.send_message(chunk)
    print("telegram review sent")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()
    run(publish=args.publish, send_telegram=True if args.telegram else None)
