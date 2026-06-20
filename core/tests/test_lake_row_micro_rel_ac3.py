# ---
# module: core.tests.test_lake_row_micro_rel_ac3
# story: US-79 AC-3 fix — _lake_row_to_micro tolerates AC-3 pre-grad rows (no 'rel')
# created-by: dev-team
# ---
"""AC-3 regression: the firehose tape sink writes pre-grad lake rows WITHOUT 'rel'.
The shared _lake_row_to_micro must not KeyError on them (it broke the cohort/candle
dashboard APIs with HTTP 500), while still using the real 'rel' when present
(offline feature parity)."""
from core.feature_extractor import _lake_row_to_micro


def test_lake_row_without_rel_does_not_crash():
    row = {  # an AC-3 pre-grad tape row — no 'rel' key
        "mint": "M", "block_time": 1_750_000_000, "slot": 10, "signature": "s",
        "price": 0.5, "side": "buy", "vol_sol": 2.0, "vol_usd": 300.0,
        "owner": "W", "phase": "pre",
    }
    micro = _lake_row_to_micro(row)
    assert micro["rel"] == 0.0
    assert micro["block_time"] == 1_750_000_000
    assert micro["vol"] == 2.0


def test_lake_row_with_rel_preserves_value():
    row = {
        "block_time": 1_750_000_050, "slot": 11, "signature": "s2", "rel": 42.0,
        "price": 0.6, "side": "sell", "vol_sol": 1.0, "owner": "W2",
    }
    micro = _lake_row_to_micro(row)
    assert micro["rel"] == 42.0  # real value preserved -> offline parity intact
