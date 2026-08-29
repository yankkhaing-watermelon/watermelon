"""Runtime settings for the self-contained Bursa scanner."""
from __future__ import annotations

import os

MARKET = "MYX"
MARKET_NAME = "Bursa Malaysia"
CURRENCY = "RM"
UNIVERSE_MODE = "TradingView Bursa equities"
USE_FULL_MARKET = True
WATCHLIST: list[str] = []
UNIVERSE = {
    "max_workers": int(os.environ.get("MAX_WORKERS", "8")),
    "batch_size": int(os.environ.get("YF_BATCH_SIZE", "80")),
}

