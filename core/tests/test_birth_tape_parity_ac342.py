# ---
# module: core.tests.test_birth_tape_parity_ac342
# sprint: sprint-8
# story: US-34 AC-34.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.helius_birth_tape_source, core.tape.mapped_source,
#               core.tape.recorder, core.tape.lake_writer, core.tape.swap_writer,
#               core.tape.lake_reader, core.feature_extractor, core.normalized_swap,
#               core.replay_source, core.clock, core.models,
#               asyncio, base64, struct, json, pathlib, types, pytest
# ---
"""AC-34.2 — birth-tape ONE code path: SAME lake + 'swaps' mirror, byte-parity.

Full population, zero selection bias, ONE code path (oracle §1/§2, Principle #2).
The Helius birth-tape source writes every token's swaps into the SAME P5 lake
(LakeWriter) + the SAME 'swaps' mirror (SwapWriter) Birdeye uses, and the SAME
US-30 FeatureExtractor reads it.  There is no birth-tape-only assembly.

This test drives the REAL TapeRecorder (the same class the live listener runs)
over a ReplaySource of banked Helius transactionNotification frames, through the
REAL decode_helius_notification mapper — exactly the chain
build_birth_tape_recorder() wires in run_listener — and proves:

  (a) the birth-tape path writes to the IDENTICAL LakeWriter + SwapWriter the
      Birdeye path uses (same recorder, same writers, source='helius_live'); and
  (b) the US-30 FeatureExtractor reads the birth-tape LAKE byte-identically to
      the DB 'swaps' MIRROR for the SAME mint — the Principle #2 proof point,
      extended to the new source.

It also verifies the AC's invariants: pre-graduation 'rel' is preserved
(negative rel for swaps before the graduation instant) and NO wallet-count
poll-stop gate is applied (every tracked mint's swaps land).

All offline, deterministic — no network.

Tests
-----
  test_birth_tape_writes_to_lake_and_db_mirror
      The recorder (helius_live) writes to BOTH the injected LakeWriter and the
      injected SwapWriter (the SAME writer types Birdeye uses).
  test_birth_tape_uses_same_writer_instances_as_birdeye
      build_birth_tape_recorder wires a LakeWriter + SwapWriter pointed at the
      SAME lake dir / 'swaps' table as build_swap_recorder.
  test_extractor_lake_vs_db_byte_identical_for_birth_tape   ← MAIN GATE
      FeatureExtractor.extract_from_lake() == extract_from_db() for the same
      mint, reading the birth-tape lake and 'swaps' mirror the recorder wrote.
  test_birth_tape_preserves_pre_grad_negative_rel
      Pre-graduation swaps keep their true negative rel (anchored to graduation).
  test_birth_tape_no_wallet_count_gate
      No selection gate: every tracked mint's landed swaps are recorded.
  test_lake_row_carries_mint_for_extractor_filter
      The lake row written by LakeWriter carries 'mint' so the extractor can
      filter per-mint (the AC-34.2 additive field).
"""
from __future__ import annotations

import asyncio
import base64
import struct
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.tape.helius_birth_tape_source import (
    TRADE_EVENT_DISCRIMINATOR,
    _b58_from_bytes,
    decode_helius_notification,
)

# ---------------------------------------------------------------------------
# Deterministic banked Helius TradeEvent fixtures
# ---------------------------------------------------------------------------

_MINT_BYTES: bytes = bytes(range(1, 33))
_MINT_B58: str = _b58_from_bytes(_MINT_BYTES)

# Graduation anchor: choose a block_time AFTER the first two swaps so they carry
# a genuine NEGATIVE pre-grad rel, and a third swap AFTER graduation (positive
# rel).  This exercises the §7.1 pre-grad-rel-preserved invariant directly.
_GRADUATED_BT: int = 1_748_000_050

# (block_time, slot, sig_suffix, is_buy, sol_amount, vsol, vtok, user_byte)
_EVENT_SPECS = [
    # pre-graduation buys (negative rel)
    (1_748_000_010, 100, "A", True, 500_000_000, 30_000_000_000, 600_000_000_000, 33),
    (1_748_000_030, 101, "B", True, 750_000_000, 31_000_000_000, 590_000_000_000, 34),
    # graduation-instant / post-graduation swaps (rel >= 0)
    (1_748_000_050, 102, "C", False, 250_000_000, 31_500_000_000, 585_000_000_000, 35),
    (1_748_000_070, 103, "D", True, 1_000_000_000, 32_000_000_000, 575_000_000_000, 36),
]


def _pack_trade_event(
    *, is_buy: bool, sol_amount: int, vsol: int, vtok: int,
    block_time: int, user_byte: int,
) -> bytes:
    """Pack a minimal Borsh TradeEvent blob (matches the AC-34.1 fixtures)."""
    user_bytes = bytes([user_byte]) + bytes(range(1, 32))
    token_amount = 1_000_000_000
    return (
        TRADE_EVENT_DISCRIMINATOR
        + _MINT_BYTES
        + struct.pack("<QQ", sol_amount, token_amount)
        + bytes([1 if is_buy else 0])
        + user_bytes
        + struct.pack("<qQQ", block_time, vsol, vtok)
    )


def _make_notification(
    *, block_time: int, slot: int, sig: str, is_buy: bool,
    sol_amount: int, vsol: int, vtok: int, user_byte: int,
) -> dict:
    """Build a synthetic Helius transactionNotification frame for the birth-tape mapper."""
    packed = _pack_trade_event(
        is_buy=is_buy, sol_amount=sol_amount, vsol=vsol, vtok=vtok,
        block_time=block_time, user_byte=user_byte,
    )
    trade_b64 = base64.b64encode(packed).decode()
    instruction = "Buy" if is_buy else "Sell"
    log_messages = [
        "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
        f"Program log: Instruction: {instruction}",
        f"Program data: {trade_b64}",
        "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
    ]
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 1,
            "result": {
                "signature": sig,
                "slot": slot,
                "transaction": {
                    "meta": {"err": None, "logMessages": log_messages},
                    "transaction": {"signatures": [sig]},
                },
            },
        },
    }


def _banked_notifications() -> list[dict]:
    """Return the banked Helius notification frames (deterministic, offline)."""
    return [
        _make_notification(
            block_time=bt, slot=slot, sig=f"BirthTapeSig{suffix}xxxxxxxxxxxxxxxxxxxx",
            is_buy=is_buy, sol_amount=sol_amount, vsol=vsol, vtok=vtok, user_byte=ub,
        )
        for (bt, slot, suffix, is_buy, sol_amount, vsol, vtok, ub) in _EVENT_SPECS
    ]


_T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
_TOKEN_STORE = {_MINT_B58: SimpleNamespace(graduated_block_time=_GRADUATED_BT)}

_MOCK_FEATURE_SET = SimpleNamespace(
    hash="ac342-birth-tape-parity-mock",
    math_version="solanabilly3:sprint-7",
)

# window large enough to admit all positive-rel swaps; pre-grad (negative-rel)
# swaps are also admitted because the causal cutoff is rel < window_s.
_WINDOW_S = 120


# ---------------------------------------------------------------------------
# Recorder runner — the REAL chain the live birth-tape listener wires
# ---------------------------------------------------------------------------


def _run_birth_tape_recorder(lake_writer, swap_writer):
    """Drive the REAL TapeRecorder over banked Helius frames via the real mapper.

    This is the EXACT chain build_birth_tape_recorder() wires in run_listener,
    with ReplaySource standing in for the live HeliusBirthTapeSource (Principle #7
    — same recorder, same mapper, same writers; only the transport differs).
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.mapped_source import MappedSwapSource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        source = MappedSwapSource(
            ReplaySource(_banked_notifications()),
            decode_helius_notification,
        )
        recorder = TapeRecorder(
            source=source,
            clock=VirtualClock(_T0),
            token_store=_TOKEN_STORE,
            swap_source="helius_live",
            swap_phase="pre",
            lake_writer=lake_writer,
            swap_writer=swap_writer,
        )
        await recorder.run()
        return recorder

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# (a) Birth-tape writes to the SAME lake + 'swaps' mirror as Birdeye
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_birth_tape_writes_to_lake_and_db_mirror(tmp_path) -> None:
    """The birth-tape recorder writes to BOTH the lake and the 'swaps' mirror.

    Uses the SAME LakeWriter + SwapWriter classes Birdeye uses (AC-19.1/19.2),
    proving ONE code path: there is no birth-tape-only writer.
    """
    from core.models import Swap
    from core.tape.lake_reader import LakeReader
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

    recorder = _run_birth_tape_recorder(LakeWriter(tmp_path), SwapWriter())

    assert len(recorder.normalized_swaps) == len(_EVENT_SPECS), (
        "Every landed birth-tape swap must be recorded (no selection gate)."
    )
    assert all(s.source == "helius_live" for s in recorder.normalized_swaps)

    # Lake: rows present and carry the helius_live provenance
    lake_rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(lake_rows) == len(_EVENT_SPECS)
    assert all(r["source"] == "helius_live" for r in lake_rows)

    # 'swaps' mirror: same count, same mint (the SAME table Birdeye writes)
    db_rows = Swap.objects.filter(mint=_MINT_B58)
    assert db_rows.count() == len(_EVENT_SPECS)


@pytest.mark.django_db
def test_birth_tape_uses_same_writer_instances_as_birdeye(tmp_path) -> None:
    """build_birth_tape_recorder wires the SAME writer types/targets as Birdeye.

    Confirms the listener wiring (run_listener) for the birth-tape path uses
    LakeWriter + SwapWriter pointed at the SAME lake dir + 'swaps' table — not a
    parallel writer (Principle #2 / oracle §1).
    """
    from core.management.commands.run_listener import (
        build_birth_tape_recorder,
        build_swap_recorder,
    )
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

    lake_dir = str(tmp_path / "lake")
    birdeye = build_swap_recorder(
        "fake-birdeye-key", _MINT_B58, graduated_block_time=_GRADUATED_BT,
        lake_base_dir=lake_dir,
    )
    birth = build_birth_tape_recorder(
        "fake-helius-key", _TOKEN_STORE, lake_base_dir=lake_dir,
    )

    # Both wire the SAME writer classes (no birth-tape-only writer).
    assert type(birth._lake_writer) is LakeWriter
    assert type(birth._swap_writer) is SwapWriter
    assert type(birdeye._lake_writer) is LakeWriter
    assert type(birdeye._swap_writer) is SwapWriter
    # SAME lake target directory.
    assert birth._lake_writer._base_dir == birdeye._lake_writer._base_dir
    # Birth-tape provenance tag.
    assert birth._swap_source == "helius_live"
    # No idle/selection gate wired on the tape (oracle §1/§2).
    assert birth._idle_monitor is None


# ---------------------------------------------------------------------------
# (b) MAIN GATE — extractor reads lake byte-identically to the DB mirror
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_extractor_lake_vs_db_byte_identical_for_birth_tape(tmp_path) -> None:
    """MAIN GATE (AC-34.2): US-30 extractor reads the birth-tape LAKE == DB MIRROR.

    The recorder writes the birth-tape to BOTH the lake (LakeWriter) and the
    'swaps' mirror (SwapWriter).  The SAME FeatureExtractor then reads each source
    for the SAME mint; the feature dicts MUST be byte-identical — the Principle #2
    proof point extended to the helius_live source.
    """
    from core.feature_extractor import FeatureExtractor
    from core.tape.lake_reader import LakeReader
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

    _run_birth_tape_recorder(LakeWriter(tmp_path), SwapWriter())

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)

    db_features = extractor.extract_from_db(_MINT_B58, window_s=_WINDOW_S)
    lake_rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    lake_features = extractor.extract_from_lake(_MINT_B58, lake_rows, window_s=_WINDOW_S)

    assert db_features is not None, "DB mirror path produced no features."
    assert lake_features is not None, "Birth-tape lake path produced no features."
    assert db_features == lake_features, (
        "Birth-tape lake vs DB-mirror features NOT byte-identical "
        "(AC-34.2 Principle #2 VIOLATION):\n"
        f"  DB:   {db_features}\n"
        f"  Lake: {lake_features}\n"
        f"  Differing keys: "
        f"{[k for k in db_features if db_features[k] != lake_features.get(k)]}"
    )


# ---------------------------------------------------------------------------
# §7.1 pre-grad rel preserved (negative allowed)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_birth_tape_preserves_pre_grad_negative_rel(tmp_path) -> None:
    """Pre-graduation swaps carry their true NEGATIVE rel anchored to graduation (§7.1).

    The first two banked swaps occur BEFORE _GRADUATED_BT, so their rel must be
    negative — never clamped to zero, never re-anchored to the first swap.
    """
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

    recorder = _run_birth_tape_recorder(LakeWriter(tmp_path), SwapWriter())
    swaps = recorder.normalized_swaps  # sorted by (block_time, slot, signature)

    # First two swaps are pre-graduation → negative rel; later swaps >= 0.
    assert swaps[0].rel == float(_EVENT_SPECS[0][0] - _GRADUATED_BT)
    assert swaps[0].rel < 0, "Pre-grad swap rel must be negative (not clamped)."
    assert swaps[1].rel < 0, "Pre-grad swap rel must be negative (not clamped)."
    assert swaps[2].rel == 0.0, "Graduation-instant swap rel must be exactly 0."
    assert swaps[3].rel > 0, "Post-grad swap rel must be positive."


# ---------------------------------------------------------------------------
# No wallet-count / selection gate on the tape (oracle §1/§2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_birth_tape_no_wallet_count_gate(tmp_path) -> None:
    """No selection gate: every tracked mint's landed swap is recorded.

    solanaBilly's wallet-count poll-stop filter is NOT applied to the tape — full
    population, zero selection bias (oracle §1/§2).  All four distinct-wallet
    swaps must land; none is dropped by any cohort/wallet-count heuristic.
    """
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

    recorder = _run_birth_tape_recorder(LakeWriter(tmp_path), SwapWriter())

    assert len(recorder.normalized_swaps) == len(_EVENT_SPECS), (
        "A swap was dropped — a selection/wallet-count gate must NOT be applied "
        "to the birth-tape (oracle §1/§2)."
    )
    assert recorder.skipped_degenerate == [], (
        "No banked swap is degenerate; none should be skipped."
    )


# ---------------------------------------------------------------------------
# The lake row carries 'mint' so the extractor can filter per-mint (AC-34.2)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_lake_row_carries_mint_for_extractor_filter(tmp_path) -> None:
    """Every birth-tape lake row carries 'mint' (the additive AC-34.2 field).

    Without 'mint' in the lake row, FeatureExtractor.extract_from_lake could not
    filter to a single token (the DB mirror filters on the indexed Swap.mint).
    This is the one schema change AC-34.2 requires for lake↔DB parity.
    """
    from core.tape.lake_reader import LakeReader
    from core.tape.lake_writer import LakeWriter
    from core.tape.swap_writer import SwapWriter

    _run_birth_tape_recorder(LakeWriter(tmp_path), SwapWriter())

    lake_rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert lake_rows, "No lake rows written."
    for row in lake_rows:
        assert row.get("mint") == _MINT_B58, (
            f"Lake row missing/incorrect 'mint': {row.get('mint')!r} "
            f"(expected {_MINT_B58!r})"
        )
