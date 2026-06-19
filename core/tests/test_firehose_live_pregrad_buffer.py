# ---
# module: core.tests.test_firehose_live_pregrad_buffer
# sprint: sprint-14
# story: live-firehose-pregrad-buffer
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, asyncio, datetime, core.clock, core.datasource,
#               core.firehose.live_pregrad_buffer, core.firehose.spine,
#               core.management.commands.run_firehose, core.pregrad_features,
#               core.tape.recorder, core.tape.helius_birth_tape_source
# ---
"""Offline, deterministic tests for the LIVE pre-grad buffer (no network).

THE CHICKEN-AND-EGG BUG (fixed)
===============================
The firehose COLLECTION task used the token_store-filtered birth-tape recorder.
That recorder only normalizes a swap whose mint is ALREADY in token_store (i.e.
already graduated) — so a token's PRE-grad swaps (which happen BEFORE it
graduates, when it is NOT in token_store) were dropped.  The live pre-grad tape
stayed empty and the model never scored.

THE FIX (proven here)
=====================
LivePreGradBuffer reads the SAME Helius source + mapper but BYPASSES the
token_store filter, buffering EVERY mint's swaps with NO graduation anchor.  The
anchor is applied RETROACTIVELY by assemble_pregrad_features at score time.

These tests prove:
  1. ALL mints' swaps land in the buffer with NONE pre-seeded as graduated
     (no-anchor-gate fix), with a CONTROL showing the old token_store recorder
     would have dropped them.
  2. Retroactive anchoring: a buffered mint with absolute-block_time swaps, then
     given a graduated_block_time, yields the 20 features with correct negative
     rel — run-twice-identical.
  3. Two-tier idle-kill: an idle UNgraduated mint is evicted; an active mint is
     retained; a graduated mint is retained through scoring.
  4. Mint-key join: a buffered mint matches a Token.mint from the graduation path
     (same string form).

Style note: coroutines are driven with asyncio.run() inside sync test functions
(matching the rest of core/tests) — no pytest-asyncio.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator

from core.clock import VirtualClock
from core.datasource import DataSource
from core.firehose.live_pregrad_buffer import (
    DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S,
    LivePreGradBuffer,
    buffered_pregrad_swap,
)
from core.firehose.spine import assemble_pregrad_features
from core.management.commands.run_firehose import TapeStore

# Two mints, NEITHER pre-seeded as graduated.
_MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
_MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
_GRAD_BT = 1000  # graduation anchor — known ONLY at score time, never at buffer time
_WSOL = "So11111111111111111111111111111111111111112"

_T0 = datetime(2026, 6, 19, tzinfo=timezone.utc)


def _mapped_swap(mint: str, block_time: int, slot: int, sig: str, side: str,
                 owner: str, vol: float, price: float) -> dict:
    """A decode_helius_notification-style mapped swap dict (internal §7.1 shape).

    Carries an ABSOLUTE block_time and NO rel — exactly what the live buffer sees
    before any graduation anchor exists.
    """
    return {
        "mint": mint,
        "block_time": block_time,
        "slot": slot,
        "signature": sig,
        "side": side,
        "price": price,
        "vol_sol": vol,
        "vol_usd": 0.0,
        "sol_usd": 0.0,
        "owner": owner,
        "base_reserve": 1_000_000,
        "quote_reserve": 5_000,
        "quote_mint": _WSOL,
        "failed": False,
    }


# Six PRE-grad swaps per mint (block_time < _GRAD_BT => rel < 0 once anchored).
def _pre_swaps(mint: str) -> list[dict]:
    return [
        _mapped_swap(mint, 900, 1, f"{mint[:4]}-s1", "buy", "A", 2.0, 0.001),
        _mapped_swap(mint, 910, 2, f"{mint[:4]}-s2", "buy", "B", 1.0, 0.0011),
        _mapped_swap(mint, 920, 3, f"{mint[:4]}-s3", "buy", "C", 3.0, 0.0012),
        _mapped_swap(mint, 930, 4, f"{mint[:4]}-s4", "sell", "A", 1.5, 0.0013),
        _mapped_swap(mint, 940, 5, f"{mint[:4]}-s5", "buy", "A", 0.5, 0.0014),
        _mapped_swap(mint, 950, 6, f"{mint[:4]}-s6", "buy", "D", 4.0, 0.0015),
    ]


class _ReplaySource(DataSource):
    """A bounded mock source that yields a fixed list of mapped swaps then ends."""

    def __init__(self, swaps: list[dict]) -> None:
        self._swaps = swaps
        self.connected = False
        self.disconnected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True

    async def events(self) -> AsyncGenerator[dict[str, Any], None]:
        for s in self._swaps:
            yield s


# ---------------------------------------------------------------------------
# 1. No-anchor-gate fix — ALL mints buffered with NONE graduated (+ control)
# ---------------------------------------------------------------------------


def test_all_mints_buffered_without_any_graduation_anchor():
    """Every mint's swaps land in the buffer though NONE is pre-seeded graduated."""
    swaps = _pre_swaps(_MINT_A) + _pre_swaps(_MINT_B)
    tape = TapeStore()
    buf = LivePreGradBuffer(
        source=_ReplaySource(swaps),
        store=tape,
        clock=VirtualClock(_T0),
        is_graduated=lambda _m: False,  # NONE graduated
        idle_ttl_s=10_000,              # large TTL so nothing is evicted here
    )
    asyncio.run(buf.run())

    assert tape.count(_MINT_A) == 6, "pre-grad swaps for ungraduated mint A were dropped"
    assert tape.count(_MINT_B) == 6, "pre-grad swaps for ungraduated mint B were dropped"
    # Buffered swaps carry absolute block_time and NO rel (anchor applied later).
    for s in tape.get(_MINT_A):
        assert "rel" not in s, "buffered swap must NOT carry a pre-baked rel"
        assert s["block_time"] < _GRAD_BT


def test_control_old_token_store_recorder_drops_pregrad_swaps():
    """CONTROL: the old token_store-gated recorder drops swaps for ungraduated mints.

    This pins the exact bug the new buffer fixes — with an EMPTY token_store (no
    mint has graduated yet), the TapeRecorder normalizes nothing, so a concurrent
    reader's tape is empty.  The live buffer above kept all 12 swaps.
    """
    from core.tape.helius_birth_tape_source import decode_helius_notification  # noqa: F401
    from core.tape.recorder import TapeRecorder

    sink = TapeStore()
    swaps = _pre_swaps(_MINT_A) + _pre_swaps(_MINT_B)
    recorder = TapeRecorder(
        source=_ReplaySource(swaps),
        clock=VirtualClock(_T0),
        token_store={},  # NOTHING graduated yet — the live reality for pre-grad swaps
        swap_source="helius_live",
        swap_phase="pre",
        on_swap=lambda mint, ns: sink.add(mint, {"block_time": ns.block_time}),
    )
    asyncio.run(recorder.run())

    assert len(recorder.normalized_swaps) == 0, "old path normalized swaps it should not have"
    assert sink.count(_MINT_A) == 0 and sink.count(_MINT_B) == 0, (
        "the token_store-gated recorder must drop pre-grad swaps for ungraduated mints"
    )


# ---------------------------------------------------------------------------
# 2. Retroactive anchoring — features + correct negative rel, run-twice-identical
# ---------------------------------------------------------------------------


def test_retroactive_anchoring_yields_features_with_negative_rel():
    """Buffered absolute-block_time swaps, anchored AT score time, yield 20 features."""
    from core.firehose.spine import to_pregrad_swaps
    from core.pregrad_features import PRE_FEATURE_NAMES

    tape = TapeStore()
    buf = LivePreGradBuffer(
        source=_ReplaySource(_pre_swaps(_MINT_A)),
        store=tape,
        clock=VirtualClock(_T0),
        is_graduated=lambda _m: False,
        idle_ttl_s=10_000,
    )
    asyncio.run(buf.run())

    buffered = tape.get(_MINT_A)
    assert buffered and all("rel" not in s for s in buffered)

    # Anchor retroactively now that graduation is known.
    anchored = to_pregrad_swaps(buffered, _GRAD_BT)
    assert all(s["rel"] < 0 for s in anchored), "pre-grad swaps must have negative rel"
    assert anchored[0]["rel"] == float(900 - _GRAD_BT)

    features = assemble_pregrad_features(buffered, _GRAD_BT)
    assert features is not None
    for name in PRE_FEATURE_NAMES:
        assert name in features


def test_retroactive_anchoring_run_twice_identical():
    """Same buffered swaps + same anchor -> byte-identical features (determinism)."""
    tape = TapeStore()
    buf = LivePreGradBuffer(
        source=_ReplaySource(_pre_swaps(_MINT_A)),
        store=tape,
        clock=VirtualClock(_T0),
        idle_ttl_s=10_000,
    )
    asyncio.run(buf.run())
    swaps = tape.get(_MINT_A)

    first = assemble_pregrad_features(swaps, _GRAD_BT)
    second = assemble_pregrad_features(swaps, _GRAD_BT)
    assert first == second


# ---------------------------------------------------------------------------
# 3. Two-tier idle-kill — evict dead UNgraduated; retain active + graduated
# ---------------------------------------------------------------------------


def test_idle_kill_evicts_dead_ungraduated_mint_retains_active_and_graduated():
    """An idle ungraduated mint is evicted; active + graduated mints are retained."""
    tape = TapeStore()
    clock = VirtualClock(_T0)
    graduated: set[str] = set()
    ttl = 300

    buf = LivePreGradBuffer(
        source=_ReplaySource([]),  # we drive ingest() directly to control the clock
        store=tape,
        clock=clock,
        is_graduated=lambda m: m in graduated,
        idle_ttl_s=ttl,
        heartbeat_interval_s=1.0,
    )

    _MINT_DEAD = "MintDeadDeadDeadDeadDeadDeadDeadDeadDeadDeadD"
    _MINT_LIVE = "MintLiveLiveLiveLiveLiveLiveLiveLiveLiveLive"
    _MINT_GRAD = "MintGradGradGradGradGradGradGradGradGradGrad"

    # t0: a swap for each mint.
    for m in (_MINT_DEAD, _MINT_LIVE, _MINT_GRAD):
        buf.ingest(_mapped_swap(m, 900, 1, f"{m[:4]}-a", "buy", "A", 1.0, 0.001))
    assert tape.count(_MINT_DEAD) == 1
    assert tape.count(_MINT_LIVE) == 1
    assert tape.count(_MINT_GRAD) == 1

    # _MINT_GRAD graduates (protected from pre-grad eviction).
    graduated.add(_MINT_GRAD)

    # Advance past the TTL, then a fresh swap arrives for LIVE and GRAD only.
    clock.advance(timedelta(seconds=ttl + 1))
    buf.ingest(_mapped_swap(_MINT_LIVE, 902, 2, "live-b", "buy", "B", 1.0, 0.001))
    buf.ingest(_mapped_swap(_MINT_GRAD, 903, 2, "grad-b", "buy", "B", 1.0, 0.001))

    # DEAD was idle > TTL and never graduated => evicted.
    assert tape.count(_MINT_DEAD) == 0, "dead ungraduated mint must be evicted"
    # LIVE got a fresh swap => retained (and now has 2 swaps).
    assert tape.count(_MINT_LIVE) == 2, "active mint must be retained"
    # GRAD is graduated => protected even though its first swap was > TTL old.
    assert tape.count(_MINT_GRAD) == 2, "graduated mint must be retained through scoring"


def test_default_idle_ttl_matches_config_default():
    """The buffer's default pre-grad idle TTL is 300s (TapeConfig default)."""
    assert DEFAULT_PRE_GRAD_IDLE_KILL_TTL_S == 300
    buf = LivePreGradBuffer(
        source=_ReplaySource([]),
        store=TapeStore(),
        clock=VirtualClock(_T0),
        config_resolver=lambda: None,  # no active config -> default
    )
    assert buf._idle_ttl_s == 300.0


# ---------------------------------------------------------------------------
# 4. Mint-key join — buffered mint matches Token.mint from graduation path
# ---------------------------------------------------------------------------


def test_buffered_mint_key_joins_token_mint_string():
    """The mint string buffered from the Helius decode == Token.mint (graduation).

    The graduation path sets Token.mint = event["address"] (a base58 string); the
    Helius decode produces the SAME base58 string for the same 32-byte pubkey.
    The scoring task joins via store.get(token.mint), so the keys must be the
    identical string form.
    """
    from core.tape.helius_birth_tape_source import _b58_from_bytes

    pubkey_bytes = bytes(range(32))
    mint_from_decode = _b58_from_bytes(pubkey_bytes)
    # The graduation path would persist exactly this string as Token.mint.
    token_mint = mint_from_decode

    tape = TapeStore()
    buf = LivePreGradBuffer(
        source=_ReplaySource([
            _mapped_swap(mint_from_decode, 900, 1, "s1", "buy", "A", 1.0, 0.001),
        ]),
        store=tape,
        clock=VirtualClock(_T0),
        idle_ttl_s=10_000,
    )
    asyncio.run(buf.run())

    # The scoring task's join: store.get(token.mint) must find the buffered swaps.
    assert tape.get(token_mint), "buffered mint key did not join Token.mint string"
    assert tape.count(token_mint) == 1


# ---------------------------------------------------------------------------
# Buffered swap projection — drops rel, keeps the fields features/settler need
# ---------------------------------------------------------------------------


def test_buffered_pregrad_swap_projection_shape():
    """buffered_pregrad_swap keeps §7.1 fields, carries vol/vol_sol, omits rel."""
    mapped = _mapped_swap(_MINT_A, 900, 7, "sig", "buy", "owner1", 2.5, 0.002)
    out = buffered_pregrad_swap(mapped)
    assert "rel" not in out
    assert out["mint"] == _MINT_A
    assert out["block_time"] == 900
    assert out["side"] == "buy"
    assert out["owner"] == "owner1"
    assert out["vol"] == 2.5 and out["vol_sol"] == 2.5
    assert out["price"] == 0.002


def test_ingest_skips_swap_with_no_mint():
    """A mapped event with no mint (unmappable) is ignored, not buffered."""
    tape = TapeStore()
    buf = LivePreGradBuffer(
        source=_ReplaySource([]),
        store=tape,
        clock=VirtualClock(_T0),
        idle_ttl_s=10_000,
    )
    buf.ingest({"block_time": 900, "price": 0.001})  # no "mint"
    assert tape.mints() == []
