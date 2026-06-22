# ---
# module: core.tests.test_firehose_postgrad_rest_backfill
# story: hotfix-postgrad-rest-entry-backfill
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: pytest, pytest-django, asyncio, unittest.mock,
#               core.backfill.birdeye_backfill, core.management.commands.run_firehose
# ---
"""Tests for the POST-grad ENTRY-tape Birdeye REST recovery (Option A).

THE BUG (root-caused live 2026-06-22): the paper entry needs a post-grad swap in
the quote window [entry-30, entry] (entry = grad + score_at_elapsed_s = grad+120s),
but the LIVE Birdeye WS post-grad subscription routinely starts AFTER that window
closed (median ~75s subscribe latency, gate-passers in the slow tail), so
`_postgrad_enterable` is structurally False and the paper leg defers forever
despite hundreds of later swaps → ZERO paper trades.

THE FIX: recover the entry window HISTORICALLY via Birdeye REST seek_by_time
(`BirdeyeBackfiller.run_for_window`), wired into the `_score_tick` paper-entry
deferral as a fire-and-forget, retrying, concurrency-capped task.

TESTING PHILOSOPHY (solanaBilly testing-philosophy-2026-05-31): every shipped
production bug lived at a MOCKED boundary — a mock encodes our ASSUMPTION of the
Birdeye payload, so code+mock agree and the bug ships (e.g. #375: real
seek_by_time items carry NO top-level volume_usd; the SOL notional is on the
quote leg). Therefore the mapper / run_for_window tests below run against a
VERBATIM CAPTURED real Birdeye payload (live read-only probe of mint
G3vNQa…pump's post-grad window, 2026-06-22), NOT invented dicts. The
fetch-task orchestration tests (retry on indexing-lag, restart-safety) mock our
OWN run_for_window return value — that boundary is our control flow, not the
external payload shape.
"""
from __future__ import annotations

import asyncio
import unittest.mock as mock
from datetime import datetime, timezone

import pytest

from core.backfill.birdeye_backfill import _map_rest_item_window
from core.clock import VirtualClock
from core.management.commands import run_firehose as rf
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
# §3 — fetch-task orchestration (OUR control flow): indexing-lag retry,
#       restart-safety. Mocks our OWN run_for_window return value.
# ---------------------------------------------------------------------------


def _daemon():
    return FirehoseDaemon(clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)))


def test_postgrad_rest_fetch_task_retries_on_empty_then_loads():
    """Birdeye indexing lag: empty on attempts 1-2, real swaps on attempt 3.

    The task must retry and, on the eventual hit, load the post-grad tape so the
    paper leg becomes enterable on a later tick.
    """
    # Align grad_bt with the clock (production epochs): entry_ts == now, so the
    # task's window-elapsed early-stop (clock >= entry+outcome_window) does NOT
    # fire and all retries run.  (A synthetic grad_bt=1000 vs a 2026 clock would
    # look like the window had closed.)
    clock_dt = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    clock_epoch = int(clock_dt.timestamp())
    daemon = FirehoseDaemon(clock=VirtualClock(clock_dt))
    mint = "MintLag"
    grad_bt = clock_epoch - 120
    entry_ts = float(grad_bt + 120)  # == clock_epoch
    real_swaps = [
        {"block_time": grad_bt + 95, "slot": 1, "signature": "a", "side": "buy",
         "price": 0.001, "rel": 95.0, "vol": 1.0, "vol_sol": 1.0, "vol_usd": 75.0, "owner": "o1"},
        {"block_time": grad_bt + 125, "slot": 2, "signature": "b", "side": "sell",
         "price": 0.0011, "rel": 125.0, "vol": 1.0, "vol_sol": 1.0, "vol_usd": 75.0, "owner": "o2"},
    ]
    calls = {"n": 0}

    def _fake_run_for_window(*a, **k):
        calls["n"] += 1
        return [] if calls["n"] < 3 else list(real_swaps)

    daemon._postgrad_rest_pending.add(mint)  # as the dispatcher would

    async def _drive():
        with mock.patch("core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
                        side_effect=_fake_run_for_window), \
             mock.patch.object(rf, "_POSTGRAD_REST_RETRY_S", 0.0):  # no real sleeps
            await daemon._postgrad_rest_fetch_task(mint, grad_bt, entry_ts, 1800.0, 75.0)

    asyncio.run(_drive())

    assert calls["n"] == 3, "must retry through the indexing-lag empties"
    assert len(daemon._postgrad_tape.get(mint)) == 2, "the hit loads the post-grad tape"
    assert mint not in daemon._postgrad_rest_pending, "dedup guard cleared in finally"


@pytest.mark.django_db(transaction=True)
def test_postgrad_rest_fetch_task_persistent_empty_keeps_token_detected():
    """Indexing lag never resolves / token illiquid: DO NOT mark SCORED/SKIPPED.

    Restart-safety: the token must stay DETECTED so a later tick re-dispatches
    while the outcome window is still open (the whole point of not permanently
    skipping a token whose post-grad swaps simply have not indexed yet).
    """
    from core.models import Token

    grad_bt = 1000
    mint = "MintStillEmpty"
    Token.objects.create(
        mint=mint, pool_address="pool_" + mint,
        graduated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        graduated_block_time=grad_bt, dex_source="pumpswap",
        raw_graduation={"mint": mint}, status="DETECTED",
    )
    daemon = _daemon()
    daemon._postgrad_rest_pending.add(mint)

    async def _drive():
        with mock.patch("core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
                        return_value=[]), \
             mock.patch.object(rf, "_POSTGRAD_REST_RETRY_S", 0.0):
            await daemon._postgrad_rest_fetch_task(mint, grad_bt, float(grad_bt + 120), 1800.0, 75.0)

    asyncio.run(_drive())

    tok = Token.objects.get(mint=mint)
    assert tok.status == "DETECTED", "persistent empty must NOT mark SCORED/SKIPPED"
    assert mint not in daemon._scored_mints
    assert not daemon._postgrad_tape.get(mint)
    assert mint not in daemon._postgrad_rest_pending, "dedup guard cleared for re-dispatch"
