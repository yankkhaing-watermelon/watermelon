"""Show what the market-breadth gate actually does to this universe.

Run this before trusting any breadth threshold. It prints the distribution of
breadth across the fetched history, how often each candidate floor would have
held the gate shut, and what the current `config.RELATIVE` settings decide today.

    python calibrate_regime.py            # fetch the live universe
    python calibrate_regime.py --min 300  # smaller pull while iterating

A threshold is only meaningful relative to the distribution it is applied to.
A 45% floor sounds moderate and can still be above this market's 80th
percentile, in which case every gated screener is off almost always.
"""
from __future__ import annotations

import argparse

import pandas as pd

import config
import data_fetcher
import relative
from indicators import enrich


def enrich_all(prices: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol, raw in prices.items():
        try:
            df = enrich(raw)
        except Exception:
            continue
        if not df.empty:
            out[str(symbol).replace(".KL", "").upper()] = df
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=0,
                    help="warn if fewer than this many symbols return history")
    args = ap.parse_args()

    prices = data_fetcher.fetch_market()
    enriched = enrich_all(prices)
    if args.min and len(enriched) < args.min:
        print(f"WARNING: only {len(enriched)} symbols usable")

    rel = getattr(config, "RELATIVE", {})
    breadth = relative.breadth_series(
        enriched,
        ma_column=str(rel.get("regime_ma_column", "ema200")),
        smooth_bars=int(rel.get("regime_smooth_bars", 5)),
    ).dropna()

    if breadth.empty:
        print("No breadth computed — check that the MA column exists and that "
              "symbols have enough history for it.")
        return

    print(f"universe        : {len(enriched)} symbols")
    print(f"breadth bars    : {len(breadth)}  "
          f"({breadth.index.min().date()} to {breadth.index.max().date()})")
    print(f"latest breadth  : {breadth.iloc[-1]:.1f}%\n")

    print("breadth distribution (% of universe above its own long MA)")
    for q in (5, 10, 25, 50, 75, 90, 95):
        print(f"  p{q:<3} {breadth.quantile(q / 100.0):6.1f}%")

    print("\nfixed-threshold behaviour — share of history the gate would be OPEN")
    for floor in (25, 30, 35, 40, 45, 50, 55, 60):
        open_pct = 100.0 * float((breadth >= floor).mean())
        flag = "  <-- current" if abs(floor - float(rel.get("regime_min_breadth_pct", 45))) < 1e-9 else ""
        print(f"  >= {floor:>2}%   open {open_pct:5.1f}% of bars{flag}")

    print("\npercentile mode — gate opens above breadth's own trailing quantile")
    for pct in (20, 30, 35, 40, 50):
        thr = relative.regime_threshold(
            breadth, mode="percentile", percentile=pct,
            lookback=int(rel.get("regime_lookback_bars", 504)),
            min_bars=int(rel.get("regime_min_bars", 120)))
        decided = thr.notna()
        if not bool(decided.any()):
            print(f"  p{pct:<3}  not enough history to decide")
            continue
        open_pct = 100.0 * float((breadth[decided] >= thr[decided]).mean())
        print(f"  p{pct:<3}  open {open_pct:5.1f}% of decided bars, "
              f"threshold now {thr.iloc[-1]:.1f}%")

    relative.attach_regime(enriched)
    print("\nwhat the current config decides today:")
    for key, value in relative.LAST_DIAGNOSTICS.items():
        print(f"  {key:<16} {value}")


if __name__ == "__main__":
    main()
