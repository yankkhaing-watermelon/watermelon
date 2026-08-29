"""Episode-based signal history used by the V5.1 browser exporter."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd

PATH = Path("app_signals_myx.csv")
COLUMNS = ["date", "symbol", "strategy", "close", "score", "first_seen", "appearances", "active", "is_new"]


def _load() -> pd.DataFrame:
    if not PATH.exists():
        return pd.DataFrame(columns=COLUMNS)
    frame = pd.read_csv(PATH, dtype={"symbol": str, "strategy": str})
    for column in COLUMNS:
        if column not in frame:
            frame[column] = "" if column in {"date", "symbol", "strategy", "first_seen"} else 0
    return frame[COLUMNS]


def load_log() -> pd.DataFrame:
    frame = _load().copy()
    if not frame.empty:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame["is_new"] = frame["is_new"].astype(str).str.lower().isin({"1", "true"})
    return frame


def update(signals: list[dict]) -> tuple[int, list[dict]]:
    today = date.today().isoformat()
    old = _load()
    active_index = {
        (str(row.symbol), str(row.strategy)): row._asdict()
        for row in old.itertuples(index=False)
        if str(row.active) in {"1", "True", "true"}
    }
    rows: list[dict] = []
    new_count = 0
    for item in signals:
        key = (str(item["symbol"]), str(item["strategy"]))
        previous = active_index.get(key)
        first_seen = str(previous.get("first_seen")) if previous else today
        appearances = int(float(previous.get("appearances") or 0)) + 1 if previous else 1
        new_count += int(previous is None)
        rows.append({"date": today, "symbol": key[0], "strategy": key[1],
                     "close": item.get("price"), "score": item.get("score"),
                     "first_seen": first_seen, "appearances": appearances, "active": 1,
                     "is_new": previous is None})
    if not old.empty:
        old["active"] = 0
        combined = pd.concat([old, pd.DataFrame(rows)], ignore_index=True)
    else:
        combined = pd.DataFrame(rows, columns=COLUMNS)
    # Keep the operational log bounded. Git retains previous versions, so an
    # ever-growing inactive history here only slows scans and bloats commits.
    dates = pd.to_datetime(combined["date"], errors="coerce")
    cutoff = pd.Timestamp(date.today() - timedelta(days=180))
    combined = combined.loc[dates.ge(cutoff) | combined["active"].astype(str).isin({"1", "True", "true"})]
    combined.to_csv(PATH, index=False)
    return new_count, rows
