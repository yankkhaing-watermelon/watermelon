"""EMA proximity list: every Bursa stock whose close is within +/- band% of
EMA 20 / 50 / 200. Independent of the six strategies (does not use screener.scan).

Reuses indicators.enrich() so the EMAs are identical to the chart EMAs in the app.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from indicators import enrich

EMA_COLS = {"20": "ema20", "50": "ema50", "200": "ema200"}


def _num(value: Any, digits: int = 3) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return round(v, digits) if math.isfinite(v) else None


def build_ema_near(by_code: dict[str, pd.DataFrame],
                   metadata: dict[str, dict[str, str]],
                   band_pct: float = 3.0,
                   min_turnover: float = 0.0) -> dict[str, Any]:
    """Scan the whole market. A stock is kept if it is within +/- band_pct of
    at least one of EMA 20/50/200. The app dropdown then picks which EMA to show.

    min_turnover: minimum median daily traded value (close x volume, last 20
    sessions, in the market currency). Drops illiquid names that sit on their
    EMAs only because they barely trade. 0 disables the filter.

    Stocks with too little history for an EMA get None for that EMA and can
    never match it (so a new listing never shows under EMA 200).
    """
    rows: list[dict[str, Any]] = []
    for sym, frame in by_code.items():
        try:
            e = enrich(frame)
            if e is None or len(e) == 0:
                continue
            last = e.iloc[-1]
            close = float(last["close"])
            if not math.isfinite(close) or close <= 0:
                continue
            if min_turnover > 0:
                tv = (e["close"] * e["volume"]).tail(20)
                med = float(tv.median()) if len(tv) else 0.0
                if not math.isfinite(med) or med < min_turnover:
                    continue
            row: dict[str, Any] = {"symbol": sym, "close": _num(close)}
            near_any = False
            for key, col in EMA_COLS.items():
                ema = float(last[col]) if col in e.columns else float("nan")
                # need a full EMA window of real bars, not just a seeded value
                if not math.isfinite(ema) or ema <= 0 or len(e) < int(key):
                    row[f"ema{key}"] = None
                    row[f"d{key}"] = None
                    continue
                dist = (close / ema - 1.0) * 100.0
                row[f"ema{key}"] = _num(ema)
                row[f"d{key}"] = round(dist, 2)
                near_any = near_any or abs(dist) <= band_pct
            if not near_any:
                continue
            prev = float(e["close"].iloc[-2]) if len(e) > 1 else 0.0
            row["change_pct"] = round((close / prev - 1.0) * 100.0, 2) if prev > 0 else 0.0
            meta = metadata.get(sym, {})
            row["name"] = str(meta.get("name") or sym)
            row["sector"] = str(meta.get("sector") or "Unclassified")
            rows.append(row)
        except Exception:
            # one bad symbol must never break the scan
            continue

    return {"band_pct": band_pct, "min_turnover": min_turnover,
            "count": len(rows), "stocks": rows}
