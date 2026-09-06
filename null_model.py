"""Null model: are the entries doing anything, or is it the exit geometry?

Every strategy in the last corrected run showed the same signature — a ~40% win
rate, a positive average and a negative median. A +15%/-7% exit breaks even
around a 32% win rate, so a profit factor slightly above 1.0 is what a coin
flip produces under this payoff. That is a hypothesis, and this module tests it
by replacing the entry rule with chance and changing nothing else.

Two nulls, because they answer different questions:

    random_all      random symbol on a random scan date. Tests the entry rule as
                    a whole — both what it picks and when it picks.
    date_matched    random symbol on the dates the strategy actually fired.
                    Holds timing fixed, so it isolates stock selection. If a
                    strategy beats random_all but not date_matched, its edge is
                    knowing when to be in the market, not which name to buy.

Everything downstream of the entry is shared with the real backtest by import,
not by copy: the same enriched frames, calendar, split, exit policies,
commission and "eod" censoring. A null computed over a different universe is not
a null, it is a second experiment.

What it reports is a percentile. If a strategy's profit factor sits at the 50th
percentile of 200 random runs, the entries contribute nothing. Convention is to
want the 95th before claiming an effect; below roughly the 80th there is no
result worth acting on.

    python null_model.py --iters 200
    python null_model.py --iters 500 --seed 7 --out null.json
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import config
import data_fetcher
import export_backtest as bt
import screener
from exits import POLICIES


def _stats(returns):
    """Win rate, mean, median and profit factor for one set of trade returns."""
    if not returns:
        return None
    gains = sum(r for r in returns if r > 0)
    losses = -sum(r for r in returns if r <= 0)
    return {
        "trades": len(returns),
        "win_rate": round(100 * sum(1 for r in returns if r > 0) / len(returns), 1),
        "avg": round(statistics.fmean(returns), 2),
        "median": round(statistics.median(returns), 2),
        "profit_factor": round(gains / losses, 3) if losses > 0 else None,
    }


def _simulate(enriched, symbols, entries, policy_name, commission):
    """Run a list of (symbol, as_of) entries through one exit policy.

    Returns net returns for trades that closed. Entries that cannot be resolved
    or that never closed are dropped, exactly as the real run drops them, so the
    realised trade count can come in below the count requested.
    """
    policy = POLICIES[policy_name]
    out = []
    for sym, as_of in entries:
        frame = enriched.get(sym)
        if frame is None:
            continue
        resolved = bt.entry_at(frame, as_of)
        if resolved is None:
            continue
        _, future, _, entry_price = resolved
        res = policy(future.iloc[1:], entry_price)
        if res.reason == "eod":      # right-censored, same rule as the real run
            continue
        out.append(res.gross_ret_pct - commission)
    return out


def _draw(rng, symbols, dates, n):
    return [(rng.choice(symbols), rng.choice(dates)) for _ in range(n)]


def _percentile(sample, value):
    """Share of the null distribution the observed value beats, as a percentile."""
    vals = [v for v in sample if v is not None]
    if not vals or value is None:
        return None
    return round(100 * sum(1 for v in vals if v < value) / len(vals), 1)


def run(iters=200, seed=1, out_path=None):
    rng = random.Random(seed)
    prices = data_fetcher.fetch_market()
    symbols = bt._liquidity_rank(prices)
    prices = {s: prices[s] for s in symbols if s in prices}
    enriched, scan_dates, split_date = bt.prepare(prices)
    live = sorted(enriched)
    commission = bt.COMMISSION_PCT * 2.0

    # Real signals first, so the null can be matched on count and on dates.
    real_entries = defaultdict(list)   # strategy -> [(symbol, as_of)]
    for as_of in scan_dates:
        for sym, frame in enriched.items():
            if bt.entry_at(frame, as_of) is None:
                continue
            pos = int(frame.index.get_indexer([as_of])[0])
            for strategy in bt.STRATEGIES:
                params = config.STRATEGIES.get(strategy)
                check = screener.CHECKS.get(strategy)
                if params is None or check is None:
                    continue
                try:
                    if check(frame, pos, params):
                        real_entries[strategy].append((sym, as_of))
                except Exception:
                    continue

    test_dates = [d for d in scan_dates if d > split_date]
    report = {}
    for strategy in bt.STRATEGIES:
        signals = [(s, d) for s, d in real_entries[strategy] if d > split_date]
        if not signals:
            continue
        strat_block = {"signals": len(signals), "policies": {}}
        for policy_name in POLICIES:
            actual = _stats(_simulate(enriched, live, signals, policy_name, commission))
            if actual is None:
                continue
            nulls = {"random_all": [], "date_matched": []}
            for _ in range(iters):
                nulls["random_all"].append(_stats(_simulate(
                    enriched, live, _draw(rng, live, test_dates, len(signals)),
                    policy_name, commission)))
                nulls["date_matched"].append(_stats(_simulate(
                    enriched, live,
                    [(rng.choice(live), d) for _, d in signals],
                    policy_name, commission)))
            block = {"actual": actual}
            for kind, runs in nulls.items():
                runs = [r for r in runs if r]
                pfs = [r["profit_factor"] for r in runs]
                block[kind] = {
                    "median_profit_factor": round(statistics.median(
                        [p for p in pfs if p is not None]), 3) if pfs else None,
                    "median_win_rate": round(statistics.median(
                        [r["win_rate"] for r in runs]), 1) if runs else None,
                    "median_median": round(statistics.median(
                        [r["median"] for r in runs]), 2) if runs else None,
                    "percentile_of_actual": _percentile(pfs, actual["profit_factor"]),
                }
            strat_block["policies"][policy_name] = block
        report[strategy] = strat_block

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "iterations": iters, "seed": seed,
        "window": {"from": str(scan_dates[0].date()), "to": str(scan_dates[-1].date()),
                   "split": str(split_date.date())},
        "universe_size": len(live),
        "note": ("Random entries run through the identical exit policies, commission "
                 "and eod censoring as the real backtest. percentile_of_actual is "
                 "where the strategy's profit factor falls in the null distribution; "
                 "50 means the entries added nothing."),
        "strategies": report,
    }
    if out_path:
        Path(out_path).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _print(payload)
    return payload


def _print(payload):
    print(f"\nNull model — {payload['iterations']} iterations, "
          f"test window {payload['window']['split']} to {payload['window']['to']}\n")
    head = f"{'strategy':18s}{'policy':17s}{'n':>5}{'actual PF':>11}{'rand PF':>9}{'pctile':>8}{'datematch':>11}{'pctile':>8}"
    print(head)
    print("-" * len(head))
    for strategy, block in payload["strategies"].items():
        for policy_name, b in block["policies"].items():
            a, ra, dm = b["actual"], b["random_all"], b["date_matched"]
            print(f"{strategy:18s}{policy_name:17s}{a['trades']:5d}"
                  f"{a['profit_factor'] if a['profit_factor'] is not None else float('nan'):11.2f}"
                  f"{ra['median_profit_factor'] or float('nan'):9.2f}"
                  f"{ra['percentile_of_actual'] if ra['percentile_of_actual'] is not None else float('nan'):8.1f}"
                  f"{dm['median_profit_factor'] or float('nan'):11.2f}"
                  f"{dm['percentile_of_actual'] if dm['percentile_of_actual'] is not None else float('nan'):8.1f}")
    print("\n50th percentile = entries added nothing. Want 95th to claim an effect.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    run(iters=args.iters, seed=args.seed, out_path=args.out)


if __name__ == "__main__":
    main()
