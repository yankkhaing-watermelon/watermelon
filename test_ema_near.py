"""EMA tab: build_ema_near exports the scan day's traded value (close x volume)."""
import numpy as np
import pandas as pd

from ema_near import build_ema_near


def _frame(price: float, volume: float, n: int = 260) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-10-02", periods=n)
    close = np.full(n, price)  # flat -> every EMA equals price -> distance 0%
    return pd.DataFrame({"open": close, "high": close, "low": close,
                         "close": close, "volume": np.full(n, volume)}, index=idx)


def test_value_is_todays_close_times_volume():
    out = build_ema_near({"AAA": _frame(0.5, 2_000_000)}, {})
    assert out["count"] == 1
    assert out["stocks"][0]["value"] == 1_000_000


def test_value_is_not_a_20_day_average():
    f = _frame(1.0, 100_000)
    f.iloc[-1, f.columns.get_loc("volume")] = 3_000_000  # spike on scan day only
    out = build_ema_near({"BBB": f}, {})
    assert out["stocks"][0]["value"] == 3_000_000


def test_zero_volume_gives_zero_value():
    out = build_ema_near({"CCC": _frame(0.2, 0)}, {})
    assert out["stocks"][0]["value"] == 0
