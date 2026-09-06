"""Tests for the backtest scoring layer."""
import pandas as pd

import export_backtest as eb


def _t(ret, phase="test", reason="target", policy="fixed_15_7", day=1):
    return eb.Trade(strategy="trending", policy=policy, symbol="X",
                    entry_date=pd.Timestamp("2026-01-01"),
                    exit_date=pd.Timestamp("2026-01-01") + pd.Timedelta(days=day),
                    ret_pct=ret, hold_days=5, phase=phase, reason=reason)


def test_phase_stats_basic():
    stats = eb._phase_stats([_t(10.0), _t(-5.0), _t(-5.0), _t(20.0)])
    assert stats["trades"] == 4
    assert stats["win_rate"] == 50.0
    assert stats["profit_factor"] == 3.0


def test_phase_stats_empty():
    """An empty phase reports zero trades rather than None, so the JSON shape
    is stable whether or not a strategy fired in that half."""
    stats = eb._phase_stats([])
    assert stats["trades"] == 0
    assert stats["profit_factor"] is None


def test_policy_payload_splits_phases():
    trades = [_t(5.0, phase="train"), _t(-7.0, phase="test"), _t(9.0, phase="test")]
    payload = eb._policy_payload("trending", "fixed_15_7", trades)
    assert payload["train"]["trades"] == 1
    assert payload["test"]["trades"] == 2
    assert payload["trades_total"] == 3


def test_policy_is_selected_on_test_profit_factor():
    """Chandelier wins on test, so it must be the published default."""
    trades = []
    for _ in range(25):
        trades += [_t(10.0, phase="train", policy="fixed_15_7"),
                   _t(-5.0, phase="train", policy="chandelier_3atr")]
    for _ in range(35):
        # both policies must book losses, or profit_factor hits its 99.0
        # sentinel for each and the comparison is a tie broken by dict order
        trades += [_t(1.0, phase="test", policy="fixed_15_7"),
                   _t(-4.0, phase="test", policy="fixed_15_7"),
                   _t(20.0, phase="test", policy="chandelier_3atr"),
                   _t(-4.0, phase="test", policy="chandelier_3atr")]
    assert eb._strategy_block("trending", trades)["chosen_policy"] == "chandelier_3atr"


def test_verdict_thin_sample():
    train = {"profit_factor": 3.0}
    test = {"trades": 6, "profit_factor": 6.6}
    assert eb._verdict(train, test, None)["level"] == "thin"


def test_verdict_bad_below_breakeven():
    train = {"profit_factor": 1.2}
    test = {"trades": 200, "profit_factor": 0.8}
    assert eb._verdict(train, test, None)["level"] == "bad"


def test_verdict_good_when_test_holds():
    train = {"profit_factor": 2.0}
    test = {"trades": 200, "profit_factor": 2.1}
    assert eb._verdict(train, test, None)["level"] == "good"


def test_verdict_warns_on_decay():
    train = {"profit_factor": 4.0}
    test = {"trades": 200, "profit_factor": 1.2}
    assert eb._verdict(train, test, None)["level"] == "warn"


def test_open_positions_are_counted():
    """Reverted behaviour: an unclosed position is booked at the final price."""
    trades = [_t(-7.0), _t(-7.0), _t(5.0), _t(90.0, reason="eod")]
    payload = eb._policy_payload("trending", "fixed_15_7", trades)
    assert payload["trades_total"] == 4
    assert payload["test"]["profit_factor"] > 6
