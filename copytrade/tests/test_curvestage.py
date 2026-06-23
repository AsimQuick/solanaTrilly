# ---
# module: copytrade.tests.test_curvestage
# epic: EPIC-copy-curvestage-integration
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# ---
"""Curve-stage cohort tests: Birdeye adapter (captured real payload), entry
features (G1 curve_frac + wallet_buy_usd), honest PnL settler, and the gate flow.

Per testing-against-reality: the adapter is tested against a VERBATIM CAPTURED
Birdeye seek_by_time payload (mint G3vNQa…, 2026-06-22) — the buy items carry a
NEGATIVE quote.uiChangeAmount and there is NO top-level volume_usd (the #375
trap), exactly the live shape.  Gate-flow + settler use deterministic inputs.
"""
from __future__ import annotations

import unittest.mock as mock

import pytest

from copytrade.curvestage_engine import handle_curvestage_buy, is_curvestage_cohort
from copytrade.curvestage_settle import settle_grad
from copytrade.entry_features import (
    FEATS,
    birdeye_items_to_owner_tape,
    entry_features,
)

# VERBATIM captured Birdeye seek_by_time items (mint G3vNQa…, 2026-06-22 live probe).
_REAL_ITEMS = [
    {"quote": {"uiAmount": 0.150471383, "price": 74.77475856632142},
     "base": {"address": "G3vNQaPxDJVgmNcqYLdP8p27ZAXVXnmUHjLdEn4pump", "price": 1.357479467043905e-05},
     "basePrice": 1.357479467043905e-05, "quotePrice": 74.77475856632142,
     "txHash": "2T9Zzk", "blockUnixTime": 1782136309, "side": "sell", "owner": "AQc3K", "txType": "swap"},
    {"quote": {"uiAmount": 0.078606552, "price": 74.77970136590142},
     "base": {"address": "G3vNQaPxDJVgmNcqYLdP8p27ZAXVXnmUHjLdEn4pump", "price": 1.3562942280863772e-05},
     "basePrice": 1.3562942280863772e-05, "quotePrice": 74.77970136590142,
     "txHash": "4Na8NP", "blockUnixTime": 1782136310, "side": "buy", "owner": "6Tqtdu", "txType": "swap"},
]


# ---------------------------------------------------------------------------
# §1 Birdeye adapter vs captured real payload (the #375 guard)
# ---------------------------------------------------------------------------


def test_birdeye_adapter_real_payload_shape():
    otr = birdeye_items_to_owner_tape(_REAL_ITEMS)
    assert len(otr) == 2
    t, price, usd, side, owner, sol = otr[0]
    assert t == 1782136309 and side == "sell" and owner == "AQc3K"
    # price = basePrice*quotePrice (USD/token), NOT a top-level field.
    assert price == pytest.approx(1.357479467043905e-05 * 74.77475856632142)
    # sol = |quote.uiAmount|, usd = sol*quotePrice.
    assert sol == pytest.approx(0.150471383)
    assert usd == pytest.approx(0.150471383 * 74.77475856632142)


def test_birdeye_adapter_drops_non_swap():
    items = list(_REAL_ITEMS) + [{**_REAL_ITEMS[0], "txType": "add_liquidity"}]
    assert len(birdeye_items_to_owner_tape(items)) == 2  # the non-swap is dropped


# ---------------------------------------------------------------------------
# §2 entry_features — G1 curve_frac + wallet_buy_usd injection
# ---------------------------------------------------------------------------


def test_entry_features_curve_frac_and_wallet_buy_usd():
    # A small synthetic on-curve tape: net 8.5 SOL in -> curve_frac = 8.5/85 = 0.10.
    otr = [
        (1000, 1e-6, 750.0, "buy", "A", 5.0),
        (1010, 1.1e-6, 525.0, "buy", "B", 3.5),
        (1020, 1.2e-6, 75.0, "sell", "A", 0.5),
        (1030, 1.3e-6, 150.0, "buy", "C", 1.0),
    ]
    f = entry_features(otr, buy_ts=1040, gts=None, wallet_buy_usd=300.0)
    assert f is not None
    # pre_sol_in = 5.0 + 3.5 - 0.5 + 1.0 = 9.0 -> curve_frac = 9/85
    assert f["pre_sol_in"] == pytest.approx(9.0)
    assert f["curve_frac"] == pytest.approx(9.0 / 85.0)
    assert f["wallet_buy_usd"] == 300.0  # FEATS[1], caller-injected (A1-1)
    assert set(FEATS).issubset(set(f.keys()))


# ---------------------------------------------------------------------------
# §3 settle_grad — honest entry + 30s post-grad VWAP exit; rejections
# ---------------------------------------------------------------------------


def test_settle_grad_graduated_basic():
    # buy at t=100; our fill = first print AFTER 100 (t=110, price 1.0).
    # graduation gts=200; post-grad VWAP over [200,230].
    tr = [
        (90, 0.9, 200.0, "buy"),
        (110, 1.0, 300.0, "buy"),   # our fill
        (150, 1.5, 100.0, "buy"),
        (205, 3.0, 500.0, "buy"),   # post-grad window [200,230]
        (215, 3.2, 300.0, "sell"),
    ]
    res = settle_grad(tr, buy_ts=100, gts=200)
    assert res["reason"] == "ok"
    assert res["graduated"] is True
    assert res["entry"] == pytest.approx(1.0)
    # VWAP over [200,230] = (3.0*500 + 3.2*300)/800; with haircuts/fees -> positive ride.
    assert res["pnl_pct"] > 0


def test_settle_grad_slip_rejected():
    # Our fill (t=110, price 2.0) is >15% above the quote (t=90, price 1.0) -> slip6002.
    tr = [(90, 1.0, 100.0, "buy"), (110, 2.0, 100.0, "buy"), (205, 3.0, 100.0, "buy")]
    res = settle_grad(tr, buy_ts=100, gts=200)
    assert res["reason"] == "slip6002"


def test_settle_grad_no_fill():
    tr = [(90, 1.0, 100.0, "buy")]  # no print after buy_ts=100
    assert settle_grad(tr, buy_ts=100, gts=200)["reason"] == "no_fill"


# ---------------------------------------------------------------------------
# §4 gate flow — handle_curvestage_buy through pass/fail (mocked Birdeye+clf)
# ---------------------------------------------------------------------------


def test_is_curvestage_cohort():
    assert is_curvestage_cohort("copy_2026-06-22_curvestage")
    assert not is_curvestage_cohort("copy_2026-06-22_copyable")
    assert not is_curvestage_cohort("")


def _event(mint="Mpump", wallet="W1", sol_amount=2.0, program=""):
    return mock.Mock(mint=mint, wallet=wallet, sol_amount=sol_amount, block_time=1000,
                     tx_type="buy", raw={"program": program})


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_buy_passes_books_observe():
    otr = [(t, 1e-6, 100.0, "buy", f"O{t}", 0.5) for t in range(400, 440, 10)]  # 4 buys, 2 SOL in
    feats = entry_features(otr, buy_ts=1000, gts=None, wallet_buy_usd=300.0)
    with mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[{"x": 1}]), \
         mock.patch("copytrade.curvestage_engine.birdeye_items_to_owner_tape", return_value=otr), \
         mock.patch("copytrade.curvestage_engine.entry_features", return_value=feats), \
         mock.patch("copytrade.curvestage_engine.get_pgrad_classifier") as gpc, \
         mock.patch("copytrade.curvestage_engine.record_score"), \
         mock.patch("copytrade.curvestage_engine.get_live_percentile_threshold", return_value=None):
        gpc.return_value.predict.return_value = 0.42
        gpc.return_value.threshold = 0.15
        gpc.return_value.threshold_source = "frozen_seed"
        pos = handle_curvestage_buy(_event(), sol_usd=150.0, cohort_id="copy_x_curvestage", strategy_id="s1")
    assert pos is not None and pos.status == "open" and pos.mode == "observe"
    assert pos.size_usd == 25.0


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_buy_gate3_blocks():
    otr = [(t, 1e-6, 100.0, "buy", f"O{t}", 0.5) for t in range(400, 440, 10)]
    feats = entry_features(otr, buy_ts=1000, gts=None, wallet_buy_usd=300.0)
    with mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[{"x": 1}]), \
         mock.patch("copytrade.curvestage_engine.birdeye_items_to_owner_tape", return_value=otr), \
         mock.patch("copytrade.curvestage_engine.entry_features", return_value=feats), \
         mock.patch("copytrade.curvestage_engine.get_pgrad_classifier") as gpc, \
         mock.patch("copytrade.curvestage_engine.record_score"), \
         mock.patch("copytrade.curvestage_engine.get_live_percentile_threshold", return_value=None):
        gpc.return_value.predict.return_value = 0.05  # below threshold
        gpc.return_value.threshold = 0.15
        gpc.return_value.threshold_source = "frozen_seed"
        pos = handle_curvestage_buy(_event(), sol_usd=150.0, cohort_id="copy_x_curvestage", strategy_id="s1")
    assert pos is None


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_buy_no_tape_fails_closed():
    with mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[]):
        pos = handle_curvestage_buy(_event(), sol_usd=150.0, cohort_id="copy_x_curvestage", strategy_id="s1")
    assert pos is None


def test_handle_curvestage_buy_post_grad_program_blocked():
    # An AMM (pump_amm) buy fails gate-1 (not on the bonding curve).
    from copytrade.buy_trigger import PUMPSWAP_AMM
    ev = _event(program=PUMPSWAP_AMM)
    assert handle_curvestage_buy(ev, sol_usd=150.0, cohort_id="copy_x_curvestage", strategy_id="s1") is None
