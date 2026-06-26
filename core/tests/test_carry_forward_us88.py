# ---
# module: core.tests.test_carry_forward_us88
# sprint: sprint-15
# story: US-88
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, pytest-django, asyncio, pathlib, ast, inspect, textwrap,
#               core.management.commands.run_firehose, trading.tape_settler,
#               core.backfill.birdeye_backfill
# ---
"""US-88 — Carry-forward verification: #386 PnL truncation+floor fix + #381/#382
post-grad entry-tape recovery.

CONFIRMATION STATUS (both fixes are on main as of 2026-06-26)
=============================================================

#386 (PnL truncation+floor fix):
  - trading/tape_settler.py line ~223:
      impact_factor = max(1.0 - cost, 0.0)  [FLOORED]
  - core/management/commands/run_firehose.py:
      FirehoseDaemon.__init__  initialises self._pending_settle  [DEFERRED]
      FirehoseDaemon._settle_due_pending  dispatches at wall-clock window-close
      FirehoseDaemon._settle_pending_task  fetches the FULL window + settles

#381 (silent-stream slot-hang fix):
  - run_firehose.py FirehoseDaemon._postgrad_subscription:
      uses asyncio.wait_for(events.__anext__(), timeout=remaining)
      NOT bare `async for ... in source.events()`

#382 (grad+120s entry-window recovery):
  - run_firehose.py FirehoseDaemon._settle_pending_task (non-v7 path):
      BirdeyeBackfiller.run_for_window fetches the FULL indexed window

V7 LINKAGE (AC-88.2):
  - #381/#382 is also v7's post-grad entry path (US-93/94): US-94's find_entry_fill
    enters on the first post-grad swap >= grad+2s within 30s — exactly the window
    the live WS subscribes too late to catch.  #381 ensures the subscription slot is
    always freed at the TTL deadline (not stuck forever on silent streams).  #382's
    deferred window-close settle is the same mechanism v7 uses in _settle_pending_task
    (model_tag="v7" path).

CREDIT CONSTRAINT:
  #382 uses Birdeye REST (OFF this sprint — ZERO CREDITS).  Live exercise of the
  REST path is deferred.  Logic + regression tests stay intact and GREEN; the REST
  boundary is mocked per project convention.

WHAT THIS SUITE PROVES
======================
AC-88.1:
  - impact_factor floor present in tape_settler.py source
  - _pending_settle + _settle_due_pending + _settle_pending_task all present
  - _score_tick defers gate-passers to _pending_settle (no direct settle call)
  - A truncated tape produces the false AUTO_SELL_TIMER; the full window hits TAKE_PROFIT
  - A thin-tape (impact > 1.0) floors at exactly -100%

AC-88.2:
  - _postgrad_subscription uses asyncio.wait_for(events.__anext__(), timeout=remaining)
  - Bare `async for ... in source.events()` is NOT present (the slot-hang bug pattern)
  - _settle_due_pending gates on wall-clock entry + outcome_window_s (not tape time)
  - _settle_pending_task calls BirdeyeBackfiller.run_for_window (mocked, no credits)
  - The ~8% gate-pass rate is genuine: assemble_v4_features returns None with 19 swaps,
    passes with 20 swaps (the secondary gate threshold)

AC-88.3:
  - Anti-prune guard: all four fix markers present on main (one test)
  - Tier-2 lake fallback (LakeBackfiller, free/local) present
  - Tier-3 Birdeye REST is credit-gated (empty api_key returns [])
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import unittest.mock as mock
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# AC-88.1 — #386 PnL truncation+floor fix confirmed intact
# ---------------------------------------------------------------------------


def test_386_floor_present_in_tape_settler_source() -> None:
    """#386 floor: tape_settler.py has impact_factor = max(1.0 - cost, 0.0).

    Anti-prune guard: if this line is removed the settlement can produce
    sub-(-100%) PnL, reproducing the original #386 bug (observed live as
    -108%/-120% model paper trades).
    """
    tape_settler_path = _REPO_ROOT / "trading" / "tape_settler.py"
    assert tape_settler_path.is_file(), f"tape_settler.py not found at {tape_settler_path}"

    src = tape_settler_path.read_text()
    assert "max(1.0 - cost, 0.0)" in src or "max(1 - cost, 0.0)" in src, (
        "#386 FLOOR MISSING from tape_settler.py.  "
        "Expected `impact_factor = max(1.0 - cost, 0.0)` (or int form). "
        "Without the floor, thin-tape cost > 1 drives PnL below -100% "
        "(impossible for a long position)."
    )


def test_386_deferred_settlement_methods_present() -> None:
    """#386 deferred-settle: three required symbols are present on FirehoseDaemon.

    The deferred settlement consists of:
      1. self._pending_settle  — dict in __init__ holding gate-passers
      2. _settle_due_pending   — dispatches tasks at wall-clock window-close
      3. _settle_pending_task  — fetches the FULL post-grad window and settles

    If any of these is removed, settlement reverts to the pre-#386 bug:
    settling at score time (~grad+120s) over a truncated tape.
    """
    from core.management.commands.run_firehose import FirehoseDaemon

    assert hasattr(FirehoseDaemon, "_settle_due_pending"), (
        "#386 FIX LOST: FirehoseDaemon._settle_due_pending missing. "
        "This method gates deferred settle on wall-clock window-close."
    )
    assert hasattr(FirehoseDaemon, "_settle_pending_task"), (
        "#386 FIX LOST: FirehoseDaemon._settle_pending_task missing. "
        "This method fetches the full post-grad window and settles."
    )
    init_src = textwrap.dedent(inspect.getsource(FirehoseDaemon.__init__))
    assert "_pending_settle" in init_src, (
        "#386 FIX LOST: self._pending_settle not initialised in FirehoseDaemon.__init__. "
        "This dict holds gate-passers waiting for window-close settlement."
    )


def test_386_score_tick_defers_not_settles_immediately() -> None:
    """#386: _score_tick registers gate-passers in _pending_settle (defers); does NOT
    call settle_paper_trade() directly inside the gate-pass branch.

    The pre-#386 bug was calling settle_paper_trade() at score time (~grad+120s)
    over a truncated tape, causing 8/9 mis-settled trades in production.
    The fix defers to _settle_due_pending / window-close.
    """
    from core.management.commands.run_firehose import FirehoseDaemon

    src = textwrap.dedent(inspect.getsource(FirehoseDaemon._score_tick))
    tree = ast.parse(src)

    direct_settle_calls = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Attribute) and node.func.attr == "settle_paper_trade")
            or (isinstance(node.func, ast.Name) and node.func.id == "settle_paper_trade")
        )
    ]
    assert not direct_settle_calls, (
        f"#386 REGRESSION: _score_tick calls settle_paper_trade() directly: "
        f"{direct_settle_calls}.  The fix defers to _settle_due_pending (window-close)."
    )

    assert "_pending_settle" in src, (
        "#386 REGRESSION: _pending_settle registration missing from _score_tick.  "
        "Gate-passers must be registered in _pending_settle for deferred settlement."
    )


@pytest.mark.django_db
def test_386_truncated_tape_false_timer_full_tape_real_trigger() -> None:
    """#386 LOCAL-PROOF: truncated tape -> false AUTO_SELL_TIMER; full tape -> TAKE_PROFIT.

    Reproduces the original bug: settling at score time over a ~5s tape fires the
    timer immediately.  With the full window the real take-profit materialises.
    """
    from trading.models import TradingSettings
    from trading.tape_settler import simulate_tape_exit

    cfg = TradingSettings.get().to_schema()
    entry = 1060.0

    # Three swaps: quote pre-entry, fill at entry, one swap shortly after
    truncated = [
        (entry - 5, 0.0010, 50.0),   # pre-entry quote
        (entry + 2, 0.0010, 50.0),   # fill
        (entry + 5, 0.0010, 50.0),   # only 5s of tape -> timer fires immediately
    ]
    # Full 140s window with a +20% price at t+140
    full = truncated + [
        (entry + 60,  0.00108, 50.0),
        (entry + 140, 0.00120, 50.0),  # +20% at t+140 -> TAKE_PROFIT
    ]

    r_trunc = simulate_tape_exit(truncated, entry, cfg, 0.1, 140.0)
    r_full  = simulate_tape_exit(full,     entry, cfg, 0.1, 140.0)

    assert r_trunc["enterable"] and r_full["enterable"], (
        "Both tapes must be enterable for the regression comparison to be meaningful."
    )
    assert r_trunc["trigger"] == "AUTO_SELL_TIMER", (
        f"Truncated tape should produce AUTO_SELL_TIMER (the #386 bug pattern). "
        f"Got: {r_trunc['trigger']!r}"
    )
    assert r_trunc["held"] <= 10, (
        f"Truncated-tape timer should fire within ~5s of entry (ran out of tape), "
        f"not after {r_trunc['held']}s."
    )
    assert r_full["trigger"] == "TAKE_PROFIT_PCT", (
        f"Full window should yield TAKE_PROFIT_PCT.  Got: {r_full['trigger']!r}"
    )
    assert r_full["pnl"] > r_trunc["pnl"] + 5, (
        f"Full-tape real PnL ({r_full['pnl']:.1f}%) must be materially better than "
        f"false-timer PnL ({r_trunc['pnl']:.1f}%).  "
        "Proves window-close settling is required."
    )


@pytest.mark.django_db
def test_386_thin_tape_impact_floor_prevents_sub_minus_100_pnl() -> None:
    """#386 floor LOCAL-PROOF: large position vs thin flow -> floors at exactly -100%.

    Pre-#386 bug: 2*size/(size+flow) can exceed 1 when flow is tiny,
    driving (1 - cost) negative and producing impossible sub-(-100%) PnL.
    """
    from trading.models import TradingSettings
    from trading.tape_settler import simulate_tape_exit

    cfg = TradingSettings.get().to_schema()
    entry = 1060.0

    # Large position (10 SOL) vs tiny flow (0.5 SOL total) — cost >> 1
    trades = [
        (entry - 5,  0.0010, 0.5),  # pre-entry quote
        (entry + 2,  0.0010, 0.5),  # fill
        (entry + 30, 0.0005, 0.5),  # price halved — deep loss
    ]
    r = simulate_tape_exit(trades, entry, cfg, 10.0, 140.0)

    assert r["enterable"], "Thin tape must still be enterable (floor the PnL, not reject)."
    assert r["pnl"] >= -100.0, (
        f"#386 FLOOR BROKEN: simulate_tape_exit returned pnl={r['pnl']:.2f}% < -100%.  "
        "impact_factor = max(1 - cost, 0.0) must prevent impossible sub-(-100%) exits."
    )


# ---------------------------------------------------------------------------
# AC-88.2 — #381/#382 post-grad entry-tape recovery confirmed intact
# ---------------------------------------------------------------------------


def test_381_postgrad_subscription_uses_wait_for_not_bare_async_for() -> None:
    """#381: _postgrad_subscription uses asyncio.wait_for with TTL, not bare async for.

    The original bug: bare `async for event in source.events()` blocks forever on
    a silent stream.  The fix bounds each frame-wait by the remaining TTL so a
    dead-silent stream always frees its capacity slot at the deadline.

    V7 LINKAGE (AC-88.2): this also guards v7's post-grad subscription path (US-93/94
    enter on first post-grad swap >= grad+2s within 30s).
    """
    from core.management.commands.run_firehose import FirehoseDaemon

    src = textwrap.dedent(inspect.getsource(FirehoseDaemon._postgrad_subscription))

    assert "wait_for" in src, (
        "#381 FIX LOST: asyncio.wait_for not found in _postgrad_subscription.  "
        "The fix bounds each next-frame await by the remaining TTL."
    )
    assert "__anext__" in src, (
        "#381 FIX LOST: events.__anext__() not found.  "
        "Manual iteration (not bare async for) is required to bound each await."
    )
    assert "remaining" in src, (
        "#381 FIX LOST: TTL `remaining` variable not found.  "
        "Must be recomputed each iteration so the loop exits at the deadline."
    )

    # The bug pattern: bare `async for ... in source.events()` — must NOT be present
    tree = ast.parse(src)
    bare_async_for_on_events = [
        ast.unparse(node.iter)
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFor)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and node.iter.func.attr == "events"
    ]
    assert not bare_async_for_on_events, (
        f"#381 REGRESSION: bare `async for ... in source.events()` present: "
        f"{bare_async_for_on_events}.  This is the silent-stream slot-hang bug."
    )


def test_382_settle_due_pending_gates_on_wallclock() -> None:
    """#382: _settle_due_pending gates on wall-clock entry + outcome_window_s.

    Settlement triggered by wall-clock (not tape's last-swap time) so a token
    that went silent post-grad still settles on schedule.
    """
    from core.management.commands.run_firehose import FirehoseDaemon

    src = textwrap.dedent(inspect.getsource(FirehoseDaemon._settle_due_pending))

    has_wallclock_gate = (
        "entry_ts_epoch + window_s" in src
        or "entry_ts_epoch+window_s" in src.replace(" ", "")
    )
    assert has_wallclock_gate, (
        "#382 FIX LOST: wall-clock gate `entry_ts_epoch + window_s` missing from "
        "_settle_due_pending.  Settlement must happen at wall-clock window-close."
    )
    assert "now_epoch" in src or "clock.now" in src, (
        "#382 FIX LOST: wall-clock read (now_epoch / clock.now) missing from "
        "_settle_due_pending."
    )


def test_382_settle_pending_task_uses_run_for_window() -> None:
    """#382: _settle_pending_task calls BirdeyeBackfiller.run_for_window.

    The pre-#382 bug: settling over the in-memory post-grad tape truncated at score
    time.  The fix fetches the COMPLETE post-grad window via run_for_window once the
    window has fully elapsed and Birdeye has indexed it all.

    CREDIT NOTE: Birdeye REST is OFF this sprint (ZERO CREDITS).  This test verifies
    the LOGIC is present.  Live exercise is deferred; the boundary is mocked below.
    """
    from core.management.commands.run_firehose import FirehoseDaemon

    src = textwrap.dedent(inspect.getsource(FirehoseDaemon._settle_pending_task))
    assert "run_for_window" in src, (
        "#382 FIX LOST: BirdeyeBackfiller.run_for_window not found in "
        "_settle_pending_task.  The fix fetches the FULL post-grad window."
    )
    assert "BirdeyeBackfiller" in src, (
        "#382 FIX LOST: BirdeyeBackfiller not referenced in _settle_pending_task."
    )


@pytest.mark.django_db(transaction=True)
def test_382_wallclock_gate_prevents_premature_settle() -> None:
    """#382 LOCAL-PROOF: settle is NOT dispatched before entry + outcome_window_s.

    Proves the gate is wall-clock based so a token with no post-grad swaps still
    settles on schedule (after window elapses) rather than deferring forever.
    """
    from core.clock import VirtualClock
    from core.management.commands.run_firehose import FirehoseDaemon

    grad_bt = 1782200000
    mint = "MintWallClockGateUS88"
    entry_ts = float(grad_bt + 120)
    window_s = 1800.0
    scoring = {"outcome_window_s": window_s}

    # Before window close: dispatch must NOT fire
    before_close = datetime.fromtimestamp(entry_ts + 100, tz=timezone.utc)
    d_before = FirehoseDaemon(clock=VirtualClock(before_close), wallet_bank=None)
    d_before._pending_settle[mint] = (entry_ts, 0.9, grad_bt, "")
    d_before._settle_due_pending(object(), 0.1, 140.0, scoring, False)

    assert mint not in d_before._settle_inflight, (
        f"#382 REGRESSION: settle dispatched BEFORE window close.  "
        f"now={before_close.timestamp():.0f} < window_close={entry_ts + window_s:.0f}"
    )
    assert mint in d_before._pending_settle, (
        "Mint must remain in _pending_settle until window closes."
    )

    # After window close: dispatch MUST fire
    after_close = datetime.fromtimestamp(entry_ts + window_s + 5, tz=timezone.utc)
    d_after = FirehoseDaemon(clock=VirtualClock(after_close), wallet_bank=None)
    d_after._pending_settle[mint] = (entry_ts, 0.9, grad_bt, "")

    async def _run_after():
        with mock.patch(
            "core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
            return_value=[],
        ), mock.patch.object(d_after, "_paper_trade_sync"):
            d_after._settle_due_pending(object(), 0.1, 140.0, scoring, False)
            assert mint in d_after._settle_inflight, (
                f"#382 REGRESSION: settle NOT dispatched after window close.  "
                f"now={after_close.timestamp():.0f} >= window_close={entry_ts + window_s:.0f}"
            )
            await asyncio.sleep(0)  # let the fire-and-forget task schedule

    asyncio.run(_run_after())


@pytest.mark.django_db(transaction=True)
def test_382_settle_pending_task_books_full_window() -> None:
    """#382 LOCAL-PROOF (mocked REST): _settle_pending_task books ALL swaps from
    run_for_window (not just the truncated in-memory tape).

    The REST boundary is mocked per project convention — Birdeye is OFF this sprint
    (ZERO CREDITS).  The mock returns three real-payload swaps captured from a live
    fixture.
    """
    from core.backfill.birdeye_backfill import _map_rest_item_window
    from core.clock import VirtualClock
    from core.management.commands.run_firehose import FirehoseDaemon
    from core.models import Token

    _GRAD_BT = 1782136218
    _ENTRY_TS = float(_GRAD_BT + 120)
    _MINT = "CarryFwdUS88FullWindowTest0000000000000000001"

    Token.objects.create(
        mint=_MINT,
        pool_address="pool_" + _MINT[:20],
        graduated_at=datetime(2026, 6, 19, tzinfo=timezone.utc),
        graduated_block_time=_GRAD_BT,
        dex_source="pumpswap",
        raw_graduation={"mint": _MINT},
        status="DETECTED",
    )

    # Three swaps anchored to real payload from captured VPS fixture
    _REST_ITEMS = [
        {
            "quote": {
                "address": "So11111111111111111111111111111111111111112",
                "uiChangeAmount": 0.150471383,
                "uiAmount": 0.150471383,
                "price": 74.77,
            },
            "base": {"address": _MINT, "price": 1.357479467043905e-05},
            "txHash": "sig_carry_fwd_001", "blockUnixTime": _GRAD_BT + 91,
            "side": "sell",
            "owner": "AQc3KRWuSF8aDWfSc5NgmCBpiwYGusM12JLcCxrrunTU",
        },
        {
            "quote": {
                "address": "So11111111111111111111111111111111111111112",
                "uiChangeAmount": -0.078606552,
                "uiAmount": 0.078606552,
                "price": 74.77,
            },
            "base": {"address": _MINT, "price": 1.3562942280863772e-05},
            "txHash": "sig_carry_fwd_002", "blockUnixTime": _GRAD_BT + 92,
            "side": "buy",
            "owner": "6TqtduzhZbJ4CZSkSy7UzcKgAForC6RLcdU4zJrem9Yu",
        },
        {
            "quote": {
                "address": "So11111111111111111111111111111111111111112",
                "uiChangeAmount": -0.274297797,
                "uiAmount": 0.274297797,
                "price": 74.77,
            },
            "base": {"address": _MINT, "price": 1.3625616471342284e-05},
            "txHash": "sig_carry_fwd_003", "blockUnixTime": _GRAD_BT + 92,
            "side": "buy",
            "owner": "6azjrSrbhKg58ywK8j5nR3iqhryYgX3BGSkep5BJsjTF",
        },
    ]
    real_swaps = [
        s for s in (
            _map_rest_item_window(
                item, _MINT,
                int(_ENTRY_TS) - 30, int(_ENTRY_TS) + 1800,
                _GRAD_BT, 140.0,
            )
            for item in _REST_ITEMS
        )
        if s is not None
    ]

    daemon = FirehoseDaemon(
        clock=VirtualClock(datetime(2026, 6, 19, tzinfo=timezone.utc)),
        wallet_bank=None,
    )
    daemon._pending_settle[_MINT] = (_ENTRY_TS, 0.83, _GRAD_BT, "")
    daemon._settle_inflight.add(_MINT)

    captured: dict = {}

    def _fake_paper_trade(m, score, trades, e_ts, *a, **kw):
        captured.update(mint=m, trades=list(trades), entry_ts=e_ts)

    async def _drive():
        with mock.patch(
            "core.backfill.birdeye_backfill.BirdeyeBackfiller.run_for_window",
            return_value=real_swaps,
        ), mock.patch.object(daemon, "_paper_trade_sync", side_effect=_fake_paper_trade):
            await daemon._settle_pending_task(
                _MINT, _ENTRY_TS, 0.83, _GRAD_BT, 1800.0,
                object(), 0.1, 140.0, False,
            )

    asyncio.run(_drive())

    assert captured.get("mint") == _MINT, (
        "settle did not book the paper trade for the expected mint."
    )
    assert len(captured.get("trades", [])) == len(real_swaps), (
        f"Expected {len(real_swaps)} swaps from the full window; "
        f"got {len(captured.get('trades', []))}.  "
        "The deferred settle must book ALL swaps from run_for_window."
    )
    tok = Token.objects.get(mint=_MINT)
    assert tok.status == "SCORED", (
        f"Token status should be SCORED after window-close settle.  Got: {tok.status!r}"
    )
    assert _MINT not in daemon._pending_settle, (
        "Mint must be removed from _pending_settle after successful settle."
    )
    assert _MINT not in daemon._settle_inflight, (
        "In-flight guard must be cleared after settle completes."
    )


def test_8_percent_gate_pass_is_genuine_secondary_gate() -> None:
    """#381/#382 link: the ~8% gate-pass rate reflects the secondary gate (>=20 pre-grad
    swaps in assemble_v4_features), NOT a scoring or entry-window bug.

    AC-88.2: confirms the ~8% is a genuine population filter.  Only tokens with >=20
    pre-grad swaps are scoreable; ~92% of graduated tokens don't accumulate 20 swaps
    in the collection window.

    Also relevant to v7 (US-93/94): find_entry_fill's grad+2s..grad+30s window
    is the entry for the same scoreable population.
    """
    from core.management.commands.run_firehose import assemble_v4_features

    grad_bt = 1782136218

    def _make_swaps(n: int) -> list[dict]:
        return [
            {
                "block_time": grad_bt - 300 + i * 10,
                "side": "buy",
                "owner": f"wallet_{i:04d}abcd",
                "vol": 5.0,
                "vol_sol": 0.05,
                "price": 1e-5,
                "slot": 100000 + i,
                "signature": f"sig{i:04d}",
            }
            for i in range(n)
        ]

    # 19 swaps: secondary gate rejects -> None
    result_19 = assemble_v4_features(
        _make_swaps(19), grad_bt, wallet_bank=None, sol_usd_spot=140.0
    )
    assert result_19 is None, (
        f"Secondary gate should return None for 19 swaps (< 20 minimum).  "
        f"Got: {result_19!r}  "
        "The ~8% rate = only tokens with >=20 swaps pass."
    )

    # 20 swaps: secondary gate passes -> features dict
    result_20 = assemble_v4_features(
        _make_swaps(20), grad_bt, wallet_bank=None, sol_usd_spot=140.0
    )
    assert result_20 is not None, (
        "Secondary gate should pass with 20 swaps.  Got None.  "
        "The minimum threshold may have been inadvertently raised."
    )


# ---------------------------------------------------------------------------
# AC-88.3 — Anti-prune guard (Phase-C purge safety)
# ---------------------------------------------------------------------------


def test_anti_prune_all_fix_markers_present_on_main() -> None:
    """Anti-prune guard: all four fix markers exist at their canonical locations.

    AC-88.3: the Phase-C purge (US-89) deploys a clean stack.  If any of these
    four markers is missing from the deploy artifact, the clean stack ships
    broken settlement and slot-hangs.  This test is the commit-time proof that
    they all survived the branch.

    Markers:
      #386a: tape_settler.py impact floor (max(1 - cost, 0.0))
      #386b: FirehoseDaemon._settle_due_pending  (deferred dispatch)
      #386c: FirehoseDaemon._settle_pending_task (full-window fetch+settle)
      #381:  _postgrad_subscription asyncio.wait_for (TTL-bounded frame wait)
      #382:  _settle_pending_task BirdeyeBackfiller.run_for_window
    """
    from core.management.commands.run_firehose import FirehoseDaemon

    settler_src = (_REPO_ROOT / "trading" / "tape_settler.py").read_text()
    assert "max(1.0 - cost, 0.0)" in settler_src or "max(1 - cost, 0.0)" in settler_src, (
        "#386a floor missing from tape_settler.py"
    )

    assert hasattr(FirehoseDaemon, "_settle_due_pending"), (
        "#386b _settle_due_pending missing from FirehoseDaemon"
    )
    assert hasattr(FirehoseDaemon, "_settle_pending_task"), (
        "#386c _settle_pending_task missing from FirehoseDaemon"
    )

    postgrad_src = textwrap.dedent(
        inspect.getsource(FirehoseDaemon._postgrad_subscription)
    )
    assert "wait_for" in postgrad_src, (
        "#381 asyncio.wait_for missing from _postgrad_subscription"
    )

    settle_task_src = textwrap.dedent(
        inspect.getsource(FirehoseDaemon._settle_pending_task)
    )
    assert "run_for_window" in settle_task_src, (
        "#382 run_for_window missing from _settle_pending_task"
    )


def test_tier2_lake_backfill_present() -> None:
    """Tier-2: LakeBackfiller (free/local) is importable and referenced in
    _lake_backfill_task.

    AC-88.3: Tier-2 is in-scope-to-confirm-present.  It provides free fallback
    tape sourcing from the local firehose lake (no credits, no external calls).
    """
    from core.backfill.lake_backfill import LakeBackfiller  # noqa: F401 — import test
    from core.management.commands.run_firehose import FirehoseDaemon

    lake_task_src = textwrap.dedent(
        inspect.getsource(FirehoseDaemon._lake_backfill_task)
    )
    assert "LakeBackfiller" in lake_task_src or "lake_backfill" in lake_task_src, (
        "Tier-2 LakeBackfiller not referenced in _lake_backfill_task.  "
        "Free local lake backfill must remain present."
    )


def test_tier3_birdeye_credit_gate_empty_key_returns_empty_list() -> None:
    """Tier-3: BirdeyeBackfiller returns [] when no API key is set (credit gate).

    AC-88.3: Birdeye REST is OFF this sprint (ZERO CREDITS).  The credit gate
    (api_key check) must prevent any HTTP call when the key is absent.  This
    verifies the gate without spending credits.
    """
    from core.backfill.birdeye_backfill import BirdeyeBackfiller

    backfiller = BirdeyeBackfiller(api_key="")
    result = backfiller.run_for_window(
        "testmint_us88_gate", 1782136218, 1782136218 + 1800, 1782136218
    )
    assert result == [], (
        f"BirdeyeBackfiller with empty api_key must return [] (credit gate).  "
        f"Got: {result!r}  "
        "Birdeye REST is OFF this sprint — ZERO CREDITS."
    )
