"""
Central configuration — tune everything here, then re-run the backtest
to see how the change affects win rate / returns BEFORE trading it live.
"""

import os

# ----------------------------------------------------------------------
# TELEGRAM
# ----------------------------------------------------------------------
# Reads from environment variables FIRST (used by GitHub Actions Secrets),
# falling back to the hardcoded value for local runs. NEVER commit real
# tokens — set them as repo Secrets on GitHub instead (see README).
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PUT_YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "PUT_YOUR_CHAT_ID_HERE")

# ----------------------------------------------------------------------
# MARKET SELECTION
# ----------------------------------------------------------------------
# One codebase, multiple markets. The MARKET env var picks which one this
# run screens (each market has its own workflow + Telegram bot + signal log).
MARKET = os.environ.get("MARKET", "MYX").upper()

MARKETS = {
    "MYX": {
        "exchange": "MYX",           # TradingView exchange code
        "scanner_slug": "malaysia",  # scanner.tradingview.com/<slug>/scan
        "currency": "RM",
        "flag": "🇲🇾",
        "name": "Bursa",
        "universe_mode": "all",      # screen every listed stock
    },
    "HKEX": {
        "exchange": "HKEX",
        "scanner_slug": "hongkong",
        "currency": "HK$",
        "flag": "🇭🇰",
        "name": "HKEX",
        "universe_mode": "top_mcap", # only the top N by market cap
        "top_n": 500,
    },
}

_M = MARKETS[MARKET]
EXCHANGE      = _M["exchange"]
SCANNER_SLUG  = _M["scanner_slug"]
CURRENCY      = _M["currency"]
MARKET_FLAG   = _M["flag"]
MARKET_NAME   = _M["name"]
UNIVERSE_MODE = _M["universe_mode"]
UNIVERSE_TOP_N = _M.get("top_n", 0)

# ----------------------------------------------------------------------
# DATA SOURCE
# ----------------------------------------------------------------------
INTERVAL   = "1D"           # daily bars
N_BARS     = 400            # history pulled per symbol (need >= 250 for EMA200)

# ----------------------------------------------------------------------
# UNIVERSE — screen the WHOLE market
# ----------------------------------------------------------------------
# When True, the screener pulls EVERY listed Bursa stock from TradingView's
# scanner (warrants/ETFs excluded), applies the filters below server-side,
# then does a cheap indicator pre-filter before downloading full history.
USE_FULL_MARKET = True

UNIVERSE = {
    "min_price": 0.01,             # effectively no price floor — screen everything
    "min_avg_value_myr": 0,        # no liquidity floor at universe level
                                   # (each STRATEGY still has its own
                                   #  min_avg_value_myr — tune those instead)
    "max_stocks": 3000,            # safety cap on scanner rows
    "use_prefilter": False,        # False = download history for EVERY stock and
                                   #  run the precise checks on all of them.
                                   #  Slower (~10-15 min/scan) but nothing slips
                                   #  under the radar.
                                   #  True = quick indicator pre-filter first
                                   #  (~3-5 min/scan, may miss borderline setups)
    "max_workers": 8,              # parallel history downloads
    "batch_size": 10,              # Yahoo fallback batch size (data_fetcher)
}

# Fallback watchlists — used when USE_FULL_MARKET = False or the scanner
# API is unreachable. STRICTLY per-market: an HKEX run can only ever fall
# back to HK symbols, never Bursa ones (and vice versa).
WATCHLISTS = {
    "MYX": [
        "MAYBANK", "PBBANK", "CIMB", "TENAGA", "PCHEM", "IHH", "PMETAL",
        "TM", "MRDIY", "SIMEPLT", "GAMUDA", "YTLPOWR", "YTL", "AXIATA",
        "CDB", "SUNWAY", "IOICORP", "KLK", "HLBANK", "RHBBANK", "MISC",
        "PETGAS", "PETDAG", "GENTING", "GENM", "MAXIS", "DIALOG", "INARI",
        "VITROX", "GREATEC", "FRONTKN", "UNISEM", "MPI", "D&O", "SKPRES",
        "PENTA", "GTRONIC", "KGB", "HARTA", "TOPGLOV", "KOSSAN", "SUPERMX",
        "AIRPORT", "CAPITALA", "WPRTS", "BIMB", "AMBANK", "MBSB", "EKOVEST",
        "IJM", "KERJAYA", "HIBISCS",
    ],
    # Large-cap HK names (TradingView HKEX symbols are plain numbers).
    # 700 Tencent, 9988 Alibaba, 5 HSBC, 1299 AIA, 941 China Mobile,
    # 3690 Meituan, 1810 Xiaomi, 2318 Ping An, 388 HKEX, 9618 JD, 1211 BYD...
    "HKEX": [
        "700", "9988", "5", "1299", "941", "3690", "1810", "2318", "388",
        "9618", "1211", "2020", "2382", "1024", "9999", "2331", "1177",
        "1", "16", "27", "66", "101", "883", "386", "857", "939", "1398",
        "3988", "2628", "2688", "6862", "268", "992", "981", "1088",
    ],
}
WATCHLIST = WATCHLISTS.get(MARKET, [])

# ----------------------------------------------------------------------
# CHARTS — candlestick charts sent to Telegram for every matched stock
# ----------------------------------------------------------------------
CHARTS = {
    "enabled": True,
    "bars": 63,            # ~3 months of trading days per chart
    "album_pause_s": 3.0,  # pause between albums (Telegram rate limits)
}

# ----------------------------------------------------------------------
# STRATEGY PARAMETERS  (used by both screener AND backtester)
# ----------------------------------------------------------------------
# Every key here is read by screener.py. `enabled` controls only the LIVE
# scan — the backtester replays whatever is in screener.CHECKS, so a strategy
# can be measured before it ever sends an alert.
STRATEGIES = {
    # 1) Established trend, still healthy
    "trending": {
        "enabled": True,
        "adx_min": 25,              # trend strength
        "rsi_min": 50,
        "rsi_max": 75,              # avoid chasing overbought
        "require_ema_stack": True,  # close > EMA20 > EMA50 > EMA200
        "min_avg_value_myr": 1_000_000,  # liquidity filter (price*volume 20d avg)
    },

    # 2) Trend just starting — EMA20 crossed above EMA50 recently
    "early_uptrend": {
        "enabled": True,
        "cross_lookback": 5,        # cross happened within last N bars
        "require_above_ema200": True,
        "volume_ratio_min": 1.2,    # vol vs 20d average
        "rsi_min": 50,
        "min_avg_value_myr": 1_000_000,
    },

    # 3) Reversal: weak momentum -> strong momentum
    "reversal": {
        "enabled": True,
        "rsi_was_below": 35,        # was oversold/weak within lookback
        "rsi_lookback": 15,
        "rsi_now_above": 50,        # momentum has flipped
        "require_macd_cross": True, # MACD line crossed above signal recently
        "macd_cross_lookback": 15,
        "require_close_above_ema20": True,
        "min_avg_value_myr": 500_000,
    },

    # 4) Stock starting to gain momentum (volume + price acceleration)
    "gaining_momentum": {
        "enabled": True,
        "volume_ratio_min": 1.8,    # today's vol vs 20d average
        "roc_period": 10,
        "roc_min": 3.0,             # % rate of change
        "macd_hist_rising_bars": 3, # MACD histogram rising N bars in a row
        "min_price": 0.15,          # skip ultra-penny noise
        "min_avg_value_myr": 500_000,
    },
    # 5) Base breakout: quiet accumulation near the lows, then a volume spike.
    #    Fires BEFORE a trend exists, unlike the four above — that is the point,
    #    and also why it behaves differently in a falling market.
    "base_breakout": {
        "enabled": True,            # live in the scan and in Telegram alerts
        "range_lookback": 60,       # bars used to define the base
        "range_position_max": 0.35, # the base must sit in the lower 35% of it
        "base_bars": 20,            # length of the quiet stretch
        "dryup_ratio_max": 0.85,    # base volume vs the stretch before it
        "volume_ratio_min": 3.0,    # spike day vs 20d average
        "min_gain_pct": 4.0,        # today's close vs yesterday's
        "close_strength_min": 0.60, # close in the top 40% of the day's range
        "require_reclaim_ema20": True,
        "reclaim_lookback": 5,      # was below EMA20 within this many bars
        "min_price": 0.15,
        "min_avg_value_myr": 500_000,
    },

    # 6) META leader: an established leader showing an institutional
    #    footprint, still close enough to its moving averages to be buyable.
    #    Continuation, not a bottom — the opposite end of the range from
    #    base_breakout, so the two can never fire on the same bar.
    "meta_leader": {
        "enabled": True,                 # live in the scan and in Telegram alerts

        "min_history": 252,              # needs a full year of bars
        "min_price": 0.15,
        "min_avg_value_myr": 1_000_000,

        # --- leadership gate
        "ema50_rise_lookback": 20,       # EMA50 higher than N bars ago
        "high_252_proximity_min": 0.85,  # within 15% of the 52-week high
        "recent_window": 20,             # consolidation window to measure past
        "rs_column": "rs_rank",          # used ONLY if that column exists
        "rs_rank_min": 80,               # percentile, when rs_rank is present
        "return_lookback": 63,           # fallback proxy: one quarter
        "return_min_pct": 15.0,          #   advance before the recent window

        # --- still buyable, not chasing
        "max_extension_ema20": 1.08,     # reject if 8%+ above EMA20
        "require_above_fast_ma": True,   # EMA10 if present, else EMA20

        # --- institutional footprint, searched across a window
        "footprint_window": 8,           # a qualifying bar within N sessions
        "volume_ratio_min": 1.8,         # that bar's vol vs 20d average
        "gain_min_pct": 2.0,             # that bar's gain
        "close_strength_min": 0.60,      # that bar closed in its top 40%
        "hold_min": 0.97,                # price still holds the footprint gain

        # --- optional tight variant. VERY sparse; leave off at first.
        "require_base": False,
        "base_depth_max": 0.25,
        "atr_fast_bars": 10,
        "atr_slow_bars": 40,
        "atr_contraction_max": 0.90,
        "breakout_buffer": 1.002,
    },
}

# ----------------------------------------------------------------------
# BACKTEST SETTINGS
# ----------------------------------------------------------------------
BACKTEST = {
    "hold_days": 20,           # max holding period per trade
    "stop_loss_pct": -7.0,     # exit if down this much
    "take_profit_pct": 15.0,   # exit if up this much
    "commission_pct": 0.15,    # per side (broker + stamp duty + clearing approx)
    "train_test_split": 0.7,   # first 70% of history = train, last 30% = test
}
