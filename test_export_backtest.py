"""Regression tests for the backtest scoring layer.

These cover the three failure modes found in the 2026-09-05 published run:
open positions marked to the final close, the exit rule being selected on the
test set, and a positive average masking a negative median.
"""
import pandas as pd

import export_backtest as eb


def _t(ret, phase="test", reason="target", policy="fixed_15_7", day=1):
    return eb.Trade(strategy="trending", policy=policy, symbol="X",
                    entry_date=pd.Timestamp("2026-01-01"),
                    exit_date=pd.Timestamp("2026-01-01") + pd.Timedelta(days=day),
                    ret_pct=ret, hold_days=5, phase=phase, reason=reason)


def test_eod_trades_are_censored():
    trades = [_t(5.0), _t(80.0, reason="eod"), _t(-7.0)]
    kept = eb._closed(trades)
    assert len(kept) == 2
    assert all(t.reason != "eod" for t in kept)


def test_trail_exit_on_last_bar_is_kept():
    """A real trail trigger is a closed trade even if it lands on the final bar."""
    assert len(eb._closed([_t(12.0, reason="trail")])) == 1


def test_open_positions_do_not_inflate_profit_factor():
    trades = [_t(-7.0), _t(-7.0), _t(5.0), _t(90.0, reason="eod")]
    payload = eb._policy_payload("trending", "chandelier_3atr", trades)
    assert payload["open_at_end"] == 1
    assert payload["trades_total"] == 3
    assert payload["test"]["profit_factor"] < 1.0  # 5 gained vs 14 lost


def test_policy_is_selected_on_train_not_test():
    """Fixed wins on train; chandelier wins on test. Train must decide."""
    trades = []
    for _ in range(25):
        trades += [_t(10.0, phase="train", policy="fixed_15_7"),
                   _t(-5.0, phase="train", policy="chandelier_3atr")]
    for _ in range(35):
        trades += [_t(1.0, phase="test", policy="fixed_15_7"),
                   _t(20.0, phase="test", policy="chandelier_3atr")]
    block = eb._strategy_block("trending", trades)
    assert block["chosen_policy"] == "fixed_15_7"


def test_negative_median_fails_even_with_high_profit_factor():
    train = {"profit_factor": 2.0}
    test = {"trades": 200, "profit_factor": 2.3, "median": -3.9, "top5_profit_share": 48.0}
    assert eb._verdict(train, test, None)["level"] == "bad"


def test_concentrated_profit_downgrades_to_warn():
    train = {"profit_factor": 2.0}
    test = {"trades": 200, "profit_factor": 2.3, "median": 1.5, "top5_profit_share": 62.0}
    v = eb._verdict(train, test, None)
    assert v["level"] == "warn"
    assert "62" in v["text"]


def test_clean_result_still_passes():
    train = {"profit_factor": 2.0}
    test = {"trades": 200, "profit_factor": 2.1, "median": 1.9, "top5_profit_share": 22.0}
    assert eb._verdict(train, test, None)["level"] == "good"


def test_thin_sample_is_flagged():
    train = {"profit_factor": 3.0}
    test = {"trades": 6, "profit_factor": 6.6, "median": 7.2, "top5_profit_share": 100.0}
    assert eb._verdict(train, test, None)["level"] == "thin"


def test_top5_share_is_reported():
    trades = [_t(100.0), _t(1.0), _t(1.0), _t(1.0), _t(1.0), _t(1.0), _t(-2.0)]
    stats = eb._phase_stats(trades)
    assert stats["top5_profit_share"] is not None
    assert stats["top5_profit_share"] > 95
