"""Fill-logic tests for the two exit policies. Bars are explicit OHLC so the
intrabar fill order is unambiguous."""
import pandas as pd
import exits


def _bars(rows):
    # rows: list of (open, high, low, close, atr14)
    idx = pd.bdate_range("2025-01-01", periods=len(rows))
    return pd.DataFrame(
        [{"open": o, "high": h, "low": l, "close": c, "atr14": a} for o, h, l, c, a in rows],
        index=idx,
    )


# ----- Policy A: fixed +15% / -7%, stop-first on intrabar tie -----
def test_fixed_target():
    f = _bars([(101, 102, 100, 101, 2), (115, 116, 114, 116, 2)])
    r = exits.policy_fixed(f, 100.0)
    assert r.reason == "target" and round(r.gross_ret_pct, 2) == 15.0

def test_fixed_stop():
    f = _bars([(99, 100, 92, 93, 2)])         # low 92 <= 93 floor
    r = exits.policy_fixed(f, 100.0)
    assert r.reason == "stop" and round(r.gross_ret_pct, 2) == -7.0

def test_fixed_stop_first_on_tie():
    f = _bars([(100, 116, 92, 100, 2)])       # bar spans both -> stop wins
    r = exits.policy_fixed(f, 100.0)
    assert r.reason == "stop" and round(r.gross_ret_pct, 2) == -7.0

def test_fixed_time_exit():
    f = _bars([(100, 101, 99, 100, 2)] * 30)  # never hits either, 20d cap
    r = exits.policy_fixed(f, 100.0)
    assert r.hold_sessions == 20 and r.reason == "time"


# ----- Policy B: -7% initial floor, then 3xATR14 trail, 250d cap -----
def test_chandelier_initial_floor():
    # Gap down to -7% before any run. ATR=2 so 3ATR=6; trail from hw=100 is 94.
    # low 93 <= max(floor 93, trail 94)=94 -> triggers; effective==trail(94) => -6? 
    # We want the -7 FLOOR to cap: construct so trail is BELOW floor early.
    # hw=100, atr=3 -> trail=91 < floor=93. low 92 <= 93 -> initial_stop -7.
    f = _bars([(99, 99, 92, 96, 3)])
    r = exits.policy_chandelier_3atr(f, 100.0)
    assert r.reason == "initial_stop" and round(r.gross_ret_pct, 2) == -7.0

def test_chandelier_trail_rides_then_exits():
    # Runs 100->130 (atr=2, 3ATR=6). Trail from peak 130 = 124.
    f = _bars([
        (105, 106, 104, 105, 2),
        (112, 113, 111, 112, 2),
        (120, 121, 119, 120, 2),
        (129, 131, 128, 130, 2),   # peak close 130
        (126, 127, 123, 124, 2),   # close 124 <= 130-6 -> trail exit
    ])
    r = exits.policy_chandelier_3atr(f, 100.0)
    assert r.reason == "trail" and round(r.gross_ret_pct, 2) == 24.0

def test_chandelier_time_cap():
    f = _bars([(100, 100.5, 99.6, 100, 2)] * 300)  # drifts, never 6 below peak
    r = exits.policy_chandelier_3atr(f, 100.0)
    assert r.reason == "time_cap" and r.hold_sessions == 250

def test_chandelier_floor_beats_wide_atr():
    # Very wide ATR would let trail sit far below; floor must still cap at -7%.
    f = _bars([(99, 99, 92, 95, 10)])   # 3ATR=30, trail=70; floor 93; low 92<=93
    r = exits.policy_chandelier_3atr(f, 100.0)
    assert r.reason == "initial_stop" and round(r.gross_ret_pct, 2) == -7.0
