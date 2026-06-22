# ---
# module: core.tests.test_firehose_postgrad_rest_backfill
# story: hotfix-postgrad-rest-entry-backfill
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: pytest, pytest-django, asyncio, unittest.mock,
#               core.backfill.birdeye_backfill, core.management.commands.run_firehose
# ---
"""Tests for the POST-grad paper settle over the Birdeye REST window.

THE BUG (root-caused live 2026-06-22): the paper leg was settled at score time
(~grad+120s) over whatever post-grad tape had arrived, but at that instant Birdeye
has only indexed swaps up to ~now — a few seconds past entry.  The settler's
AUTO_SELL_TIMER fallback then booked at the last available swap (held ~3-14s) and
silently missed the real later TAKE_PROFIT / RUG_PULL that plays out over the
outcome window (proven live: 8 of 9 trades mis-settled).

THE FIX: DEFER the settle until wall-clock passes entry + outcome_window_s, then
settle ONCE over a single fresh full-window Birdeye REST fetch
(`BirdeyeBackfiller.run_for_window`, fully indexed by then).  Restart-safe: the
token stays DETECTED until the settle actually fires.

TESTING PHILOSOPHY (solanaBilly testing-philosophy-2026-05-31): every shipped
production bug lived at a MOCKED boundary — a mock encodes our ASSUMPTION of the
Birdeye payload, so code+mock agree and the bug ships (e.g. #375: real
seek_by_time items carry NO top-level volume_usd; the SOL notional is on the
quote leg). Therefore the mapper / run_for_window tests below run against a
VERBATIM CAPTURED real Birdeye payload (live read-only probe of mint
G3vNQa…pump's post-grad window, 2026-06-22), NOT invented dicts. The
deferred-settle orchestration tests (window-close gate, full-window settle,
error-retry) mock our OWN run_for_window return value / settle seam — that
boundary is our control flow, not the external payload shape.
"""
from __future__ import annotations

import asyncio
import unittest.mock as mock
from datetime import datetime, timezone

import pytest

from core.backfill.birdeye_backfill import _map_rest_item_window
from core.clock import VirtualClock
from core.management.commands.run_firehose import FirehoseDaemon

# ---------------------------------------------------------------------------
# CAPTURED REAL Birdeye seek_by_time items — verbatim from a live read-only probe
# of mint G3vNQaPxDJVgmNcqYLdP8p27ZAXVXnmUHjLdEn4pump (symbol ARX) on 2026-06-22.
# graduated_block_time = 1782136218; entry_ts = grad+120 = 1782136338; the three
# items sit in the entry quote window [entry-30, entry] = [1782136308, 1782136338]
# at block_times 309/310 (rel = 91/92). Note item[1]/item[2] are BUYS whose
# quote.uiChangeAmount is NEGATIVE (-0.078, -0.274) — the live shape that requires
# abs() in the mapper (a synthetic fixture would have hidden this).
# ---------------------------------------------------------------------------
_GRAD_BT = 1782136218
_ENTRY_TS = _GRAD_BT + 120  # 1782136338
_MINT = "G3vNQaPxDJVgmNcqYLdP8p27ZAXVXnmUHjLdEn4pump"

_REAL_ITEMS = [
    {
        "quote": {"address": "So11111111111111111111111111111111111111112",
                  "uiChangeAmount": 0.150471383, "uiAmount": 0.150471383,
                  "price": 74.77475856632142},
        "base": {"address": _MINT, "price": 1.357479467043905e-05},
        "txHash": "2T9ZzkcFKbPP8osN4YmkpFfSLVzNvQ9pr77sszeCLcRHovs2F2QTYfrXBVVSuVQWXsqbcxt53XurQx563ee7Pep1",
        "blockUnixTime": 1782136309, "side": "sell",
        "owner": "AQc3KRWuSF8aDWfSc5NgmCBpiwYGusM12JLcCxrrunTU",
    },
    {
        "quote": {"address": "So11111111111111111111111111111111111111112",
                  "uiChangeAmount": -0.078606552, "uiAmount": 0.078606552,
                  "price": 74.77970136590142},
        "base": {"address": _MINT, "price": 1.3562942280863772e-05},
        "txHash": "4Na8NPGUVx7hpPPsyf6gN3pztkFBqJKtpceneaSScfeCXVqxa9a5z5RKJAWA2c8aDHR4Tud2QVvxxFQC4rx5xweK",
        "blockUnixTime": 1782136310, "side": "buy",
        "owner": "6TqtduzhZbJ4CZSkSy7UzcKgAForC6RLcdU4zJrem9Yu",
    },
    {
        "quote": {"address": "So11111111111111111111111111111111111111112",
                  "uiChangeAmount": -0.274297797, "uiAmount": 0.274297797,
                  "price": 74.77970136590142},
        "base": {"address": _MINT, "price": 1.3625616471342284e-05},
        "txHash": "2bqSzF3Kf8MeE8YNeFUTvqStC6N8vKPVohZKVFhLKx8xh4tGLfbzVjrsX8nSFa5MqZrCTaZLmgXKKd97LrVjZFPm",
        "blockUnixTime": 1782136310, "side": "buy",
        "owner": "6azjrSrbhKg58ywK8j5nR3iqhryYgX3BGSkep5BJsjTF",
    },
]


# ---------------------------------------------------------------------------
# §1 — mapper vs CAPTURED REAL payload (the #375-class guard)
# ---------------------------------------------------------------------------


def test_map_rest_item_window_against_real_birdeye_item():
    """Map the real sell item; assert the post-grad settler shape is correct."""
    t_from, t_to = _ENTRY_TS - 30, _ENTRY_TS + 1800
    out = _map_rest_item_window(_REAL_ITEMS[0], _MINT, t_from, t_to, _GRAD_BT, sol_usd_spot=75.0)
    assert out is not None
    assert out["block_time"] == 1782136309 and isinstance(out["block_time"], int)
    assert out["rel"] == float(1782136309 - _GRAD_BT)  # 91 — post-grad
    assert out["side"] == "sell"
    assert out["signature"] == _REAL_ITEMS[0]["txHash"]
    # price from base.price (USD/token), NOT a top-level field (#375).
    assert out["price"] == 1.357479467043905e-05
    # vol_sol from the SOL quote leg; vol alias present for the settler fallback.
    assert out["vol_sol"] == pytest.approx(0.150471383)
    assert out["vol"] == pytest.approx(0.150471383)
    # vol_usd uses the caller's cached spot (no fresh fetch — stale-spot lesson).
    assert out["vol_usd"] == pytest.approx(0.150471383 * 75.0)


def test_map_rest_item_window_abs_on_negative_quote_change_real_buy():
    """Real BUY items carry a NEGATIVE quote.uiChangeAmount — vol_sol must abs()."""
    t_from, t_to = _ENTRY_TS - 30, _ENTRY_TS + 1800
    out = _map_rest_item_window(_REAL_ITEMS[1], _MINT, t_from, t_to, _GRAD_BT, sol_usd_spot=None)
    assert out is not None
    assert out["side"] == "buy"
    assert out["vol_sol"] == pytest.approx(0.078606552)  # abs(-0.078606552)
    # No spot passed -> sol_usd falls back to the item's own quote-leg price.
    assert out["sol_usd"] == pytest.approx(74.77970136590142)


def test_map_rest_item_window_drops_out_of_window():
    """An item outside [t_from, t_to] maps to None (belt-and-suspenders filter)."""
    # Window entirely after the item's block_time.
    out = _map_rest_item_window(_REAL_ITEMS[0], _MINT, 1782200000, 1782200300, _GRAD_BT, sol_usd_spot=None)
    assert out is None


# ---------------------------------------------------------------------------
# §2 — run_for_window pagination/sort/window over CAPTURED REAL items
# ---------------------------------------------------------------------------


def test_run_for_window_returns_sorted_postgrad_swaps_real_items():
    """run_for_window over the real items: all in-window, rel-anchored, sorted."""
    from core.backfill.birdeye_backfill import BirdeyeBackfiller

    backfiller = BirdeyeBackfiller(api_key="test-key")  # avoid settings lookup
    t_from, t_to = _ENTRY_TS - 30, _ENTRY_TS + 300

    # One short page of REAL items (len < 100 → loop terminates after it).
    body = {"data": {"items": list(_REAL_ITEMS)}}
    with mock.patch("core.backfill.birdeye_backfill._be_get", return_value=body):
        swaps = backfiller.run_for_window(_MINT, t_from, t_to, _GRAD_BT, sol_usd_spot=75.0)

    assert len(swaps) == 3, "all three real in-window items map cleanly"
    # Sorted ascending by (block_time, slot, signature) — parity ordering.
    bts = [s["block_time"] for s in swaps]
    assert bts == sorted(bts)
    # All post-grad (rel >= 0) so they drop into _postgrad_tape unchanged.
    assert all(s["rel"] >= 0 for s in swaps)
    assert all(s["vol_usd"] > 0 for s in swaps)


def test_run_for_window_missing_api_key_returns_empty():
    """No BIRDEYE_API_KEY → graceful no-op (never raises, never burns credits)."""
    from core.backfill.birdeye_backfill import BirdeyeBackfiller

    backfiller = BirdeyeBackfiller(api_key="")
    swaps = backfiller.run_for_window(_MINT, _ENTRY_TS - 30, _ENTRY_TS + 300, _GRAD_BT)
    assert swaps == []


# ---------------------------------------------------------------------------
# §3 — deferred-settle orchestration (OUR control flow): settle at window close
#       over the FULL fetched tape, the wall-clock deferral gate, and error-retry
#       safety. Mocks our OWN run_for_window return value / settle seam — never the
#       external Birdeye payload shape (which §1/§2 pin against captured reality).
# ---------------------------------------------------------------------------


def _daemon():
    return FirehoseDaemon(clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)))


@pytest.mark.django_db(transaction=True)
def test_settle_pending_task_settles_full_window_then_marks_terminal():
    """At window close the deferred settle fetches the FULL window ONCE and books
    the paper leg over the COMPLETE tape, then marks the token terminal (SCORED)
    and clears it from the pending / in-flight sets.

    Reality-anchored: run_for_window returns swaps mapped from the VERBATIM captured
    Birdeye payload (_REAL_ITEMS) via the real mapper; we mock only our OWN control
    flow (the settle seam) — never the external payload shape.
    """
    from core.models import Token

    Token.objects.create(
        mint=_MINT, pool_address="pool_" + _MINT,
        graduated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        graduated_block_time=_GRAD_BT, dex_source="pumpswap",
        raw_graduation={"mint": _MINT}, status="DETECTED",
    )
    real_swaps = [
        m for m in (
            _map_rest_item_window(it, _MINT, _ENTRY_TS - 30, _ENTRY_TS + 1800, _GRAD_BT, 140.0)
            for it in _REAL_ITEMS
        ) if m
    ]
    daemon = _daemon()
    daemon._pending_settle[_MINT] = (float(_ENTRY_TS), 0.83, _GRAD_BT)
    daemon._settle_inflight.add(_MINT)  # as _settle_due_pending would have
    captured = {}

    def _fake_paper_trade_sync(m, score, trades, e_ts, *a, **k):
        captured.update(mint=m, trades=trades, entry_ts=e_ts)

    async def _drive():
        with mock.patch("core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
                        return_value=real_swaps), \
             mock.patch.object(daemon, "_paper_trade_sync", side_effect=_fake_paper_trade_sync):
            await daemon._settle_pending_task(
                _MINT, float(_ENTRY_TS), 0.83, _GRAD_BT, 1800.0, object(), 0.1, 140.0, False,
            )

    asyncio.run(_drive())

    assert captured["mint"] == _MINT
    assert len(captured["trades"]) == 3, "settles over the FULL fetched window"
    assert Token.objects.get(mint=_MINT).status == "SCORED", "terminal after settle"
    assert _MINT in daemon._scored_mints
    assert _MINT not in daemon._pending_settle, "popped after a successful settle"
    assert _MINT not in daemon._settle_inflight, "in-flight guard cleared in finally"


@pytest.mark.django_db(transaction=True)
def test_settle_pending_task_transient_error_keeps_mint_queued_for_retry():
    """A transient fetch/settle error must NOT lose the trade: the mint stays in
    _pending_settle (re-tried next tick), is NOT marked SCORED, and the in-flight
    guard is cleared so the retry can dispatch."""
    from core.models import Token

    grad_bt, mint = 1000, "MintBoom"
    Token.objects.create(
        mint=mint, pool_address="pool_" + mint,
        graduated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        graduated_block_time=grad_bt, dex_source="pumpswap",
        raw_graduation={"mint": mint}, status="DETECTED",
    )
    daemon = _daemon()
    entry_ts = float(grad_bt + 120)
    daemon._pending_settle[mint] = (entry_ts, 0.9, grad_bt)
    daemon._settle_inflight.add(mint)

    async def _drive():
        with mock.patch("core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
                        side_effect=RuntimeError("birdeye 500")):
            await daemon._settle_pending_task(
                mint, entry_ts, 0.9, grad_bt, 1800.0, object(), 0.1, 140.0, False,
            )

    asyncio.run(_drive())

    assert Token.objects.get(mint=mint).status == "DETECTED", "transient error stays DETECTED"
    assert mint not in daemon._scored_mints
    assert mint in daemon._pending_settle, "stays queued for a later-tick retry"
    assert mint not in daemon._settle_inflight, "in-flight cleared so the retry can dispatch"


@pytest.mark.django_db(transaction=True)
def test_settle_due_pending_gates_on_wallclock_window_close():
    """The settle fires on WALL-CLOCK (entry + outcome_window_s), NOT on the tape's
    own last swap — so a token that went dead post-grad still settles on schedule.
    Before the window closes nothing dispatches; after, the mint is taken in-flight.
    """
    grad_bt, mint = 1782200000, "MintWait"
    entry_ts = float(grad_bt + 120)
    window_s = 1800.0
    scoring = {"outcome_window_s": window_s}

    before = datetime.fromtimestamp(entry_ts + 100, tz=timezone.utc)
    d1 = FirehoseDaemon(clock=VirtualClock(before))
    d1._pending_settle[mint] = (entry_ts, 0.9, grad_bt)
    d1._settle_due_pending(object(), 0.1, 140.0, scoring, False)
    assert mint not in d1._settle_inflight, "must NOT settle before the window closes"
    assert mint in d1._pending_settle

    after = datetime.fromtimestamp(entry_ts + window_s + 5, tz=timezone.utc)
    d2 = FirehoseDaemon(clock=VirtualClock(after))
    d2._pending_settle[mint] = (entry_ts, 0.9, grad_bt)

    async def _drive_after():
        with mock.patch("core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
                        return_value=[]), \
             mock.patch.object(d2, "_paper_trade_sync"):
            d2._settle_due_pending(object(), 0.1, 140.0, scoring, False)
            assert mint in d2._settle_inflight, "taken in-flight once the window closed"
            await asyncio.sleep(0)  # let the dispatched task run + clean up

    asyncio.run(_drive_after())


@pytest.mark.django_db
def test_truncated_tape_mislabels_timer_full_tape_recovers_real_trigger():
    """The truncation bug at the SETTLER boundary: a tape that ENDS a few seconds
    past entry yields a false AUTO_SELL_TIMER at the last print; the SAME settler
    over the full window sees the REAL take-profit.  This is exactly why score-time
    settling was wrong and window-close settling is right (live: 8/9 mis-settled).
    """
    from trading.models import TradingSettings
    from trading.tape_settler import simulate_tape_exit

    cfg = TradingSettings.get().to_schema()  # tp12tr15_t1200 (tp=12, timer=1200, sl=None)
    entry = 1060.0
    quote = (entry - 5, 0.0010, 50.0)
    fill = (entry + 2, 0.0010, 50.0)
    truncated = [quote, fill, (entry + 5, 0.0010, 50.0)]          # ends ~5s past entry
    full = truncated + [(entry + 60, 0.00108, 50.0),
                        (entry + 140, 0.00120, 50.0)]             # +20% -> TP(+12%) fires
    r_trunc = simulate_tape_exit(truncated, entry, cfg, 0.1, 140.0)
    r_full = simulate_tape_exit(full, entry, cfg, 0.1, 140.0)

    assert r_trunc["enterable"] and r_full["enterable"]
    assert r_trunc["trigger"] == "AUTO_SELL_TIMER", "truncated tape -> false timer at last print"
    assert r_trunc["held"] <= 10, "the 'timer' actually fired seconds past entry (ran out of tape)"
    assert r_full["trigger"] == "TAKE_PROFIT_PCT", "the full window sees the real take-profit"
    assert r_full["pnl"] > r_trunc["pnl"] + 5, "the real outcome is materially different"


@pytest.mark.django_db
def test_thin_tape_impact_haircut_floors_pnl_at_minus_100():
    """Thin-tape guard (bug 1): when the depth/impact cost 2S/(S+flow) exceeds 1,
    PnL must floor at -100% (a long loses AT MOST 100%) — never the impossible
    sub-(-100%) / negative-exit-price values the unfloored haircut produced live
    (e.g. -108% / -120%).
    """
    from trading.models import TradingSettings
    from trading.tape_settler import simulate_tape_exit

    cfg = TradingSettings.get().to_schema()
    entry = 1060.0
    # Large position vs a tiny flow (thin tape): size_usd = 10*140 = 1400; flow ~ $1.5
    # -> cost = 2*1400/(1400+1.5) ~ 1.998 -> unfloored (1-cost) ~ -0.998 -> sub-(-100%).
    quote = (entry - 5, 0.0010, 0.5)
    fill = (entry + 2, 0.0010, 0.5)
    drop = (entry + 30, 0.0005, 0.5)  # price halves -> a real loss on top of the haircut
    r = simulate_tape_exit([quote, fill, drop], entry, cfg, 10.0, 140.0)
    assert r["enterable"]
    assert r["pnl"] >= -100.0, "a long position can never settle worse than a total loss"
