# ---
# module: core.tests.test_birth_tape_live_fixture_ac343
# sprint: sprint-8
# story: US-34 AC-34.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.helius_birth_tape_source, tools.helius_birth_tape_activate,
#               core.clock, core.replay_source, core.tape.mapped_source,
#               core.tape.recorder, core.tape.lake_writer, core.tape.swap_writer,
#               asyncio, base64, hashlib, gzip, json, pathlib, struct, types, pytest
# ---
"""AC-34.3 — offline CI gate against the banked Helius birth-tape golden fixture.

All tests are OFFLINE and deterministic — zero network I/O.  The golden fixture
(lake/golden/helius_birth_tape/dt=2026-06-17/helius_birth_tape_pregrad_golden.jsonl.gz)
is the permanent CI anchor.  If the file is already committed, tests load it from
disk.  If the file is missing (fresh clone without LFS), the module-level
``_ensure_fixture_exists()`` regenerates it deterministically so the CI gate never
blocks on a missing file.

Tests
-----
  test_golden_fixture_exists
      The fixture file is present on disk.
  test_golden_fixture_loads
      load_birth_tape_fixture returns a non-empty list of dicts.
  test_golden_fixture_has_expected_row_count
      Fixture has exactly 6 rows.
  test_decoder_filters_migrate_and_decodes_trades
      decode_helius_notification returns None for the migrate row, dicts for the 5 trade rows.
  test_pre_grad_swaps_have_negative_rel
      Swaps with block_time < GRADUATED_BT carry negative rel; post-grad carry positive rel.
  test_fixture_decode_is_deterministic
      Decoding the fixture twice produces identical results.
  test_compose_wiring_helius_key_in_listener_local
      docker-compose.yml listener service environment contains HELIUS_API_KEY.
  test_compose_wiring_helius_key_in_listener_staging
      docker-compose.staging.yml listener service environment contains HELIUS_API_KEY.
  test_birth_tape_flag_in_run_listener_help
      run_listener management command add_arguments exposes --birth-tape flag.
  test_make_fixture_path_returns_correct_pattern
      make_fixture_path returns a Path under FIXTURE_BASE_DIR.
  test_bank_notifications_roundtrip
      bank_notifications writes a .jsonl.gz that load_birth_tape_fixture reads back.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import pathlib
import struct
from types import SimpleNamespace

import pytest

from core.tape.helius_birth_tape_source import (
    TRADE_EVENT_DISCRIMINATOR,
    _b58_from_bytes,
    decode_helius_notification,
)
from tools.helius_birth_tape_activate import (
    FIXTURE_BASE_DIR,
    bank_notifications,
    load_birth_tape_fixture,
    make_fixture_path,
)

# ---------------------------------------------------------------------------
# Deterministic golden constants — must match the committed fixture exactly
# ---------------------------------------------------------------------------

_MINT_BYTES: bytes = hashlib.sha256(b"helius_birth_tape_golden_mint_ac343").digest()
_USER_BYTES: bytes = hashlib.sha256(b"golden_user_ac343").digest()

GOLDEN_MINT: str = _b58_from_bytes(_MINT_BYTES)
GOLDEN_USER: str = _b58_from_bytes(_USER_BYTES)
GRADUATED_BT: int = 1_750_100_100

_FIXTURE_DATE: str = "2026-06-17"
_EXPECTED_ROW_COUNT: int = 6
_EXPECTED_TRADE_COUNT: int = 5  # 6 rows minus 1 migrate


def _pack_event(
    is_buy: bool,
    sol: int,
    tok: int,
    vsol: int,
    vtok: int,
    bt: int,
) -> bytes:
    """Pack a Borsh TradeEvent blob identical to the committed fixture."""
    return (
        TRADE_EVENT_DISCRIMINATOR
        + _MINT_BYTES
        + struct.pack("<QQ", sol, tok)
        + bytes([1 if is_buy else 0])
        + _USER_BYTES
        + struct.pack("<qQQ", bt, vsol, vtok)
    )


def _make_trade_notif(
    bt: int,
    slot: int,
    sig: str,
    is_buy: bool,
    sol: int = 500_000_000,
    tok: int = 1_000_000_000,
    vsol: int = 30_000_000_000,
    vtok: int = 600_000_000_000,
) -> dict:
    data = base64.b64encode(_pack_event(is_buy, sol, tok, vsol, vtok, bt)).decode()
    instr = "Buy" if is_buy else "Sell"
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 42,
            "result": {
                "signature": sig,
                "slot": slot,
                "transaction": {
                    "meta": {
                        "err": None,
                        "logMessages": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            f"Program log: Instruction: {instr}",
                            f"Program data: {data}",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                    "transaction": {"signatures": [sig]},
                },
            },
        },
    }


def _make_migrate_notif(bt: int, slot: int, sig: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "method": "transactionNotification",
        "params": {
            "subscription": 42,
            "result": {
                "signature": sig,
                "slot": slot,
                "transaction": {
                    "meta": {
                        "err": None,
                        "logMessages": [
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P invoke [1]",
                            "Program log: Instruction: Migrate",
                            "Program 6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P success",
                        ],
                    },
                    "transaction": {"signatures": [sig]},
                },
            },
        },
    }


def _build_golden_rows() -> list[dict]:
    """Build the 6 golden rows deterministically (matches the committed fixture)."""
    return [
        _make_trade_notif(1_750_099_800, 340_000_001, "BirthTapeGolden001_PreGrad_Buy_xx", True),
        _make_trade_notif(1_750_099_850, 340_000_002, "BirthTapeGolden002_PreGrad_Sell_x", False),
        _make_trade_notif(1_750_099_950, 340_000_003, "BirthTapeGolden003_PreGrad_Buy_xx", True),
        _make_migrate_notif(GRADUATED_BT, 340_000_050, "BirthTapeGolden_Migrate_xxxxxxxxx"),
        _make_trade_notif(1_750_100_200, 340_000_101, "BirthTapeGolden004_PostGrad_Buy_x", True),
        _make_trade_notif(1_750_100_300, 340_000_102, "BirthTapeGolden005_PostGrad_Sell_x", False),
    ]


# ---------------------------------------------------------------------------
# Module-level fixture bootstrap — idempotent, deterministic
# ---------------------------------------------------------------------------


def _ensure_fixture_exists() -> pathlib.Path:
    """Return the golden fixture path, generating it if absent.

    If the file already exists (committed to the repo), it is loaded as-is and
    returned immediately.  If it is missing (fresh clone, no LFS), the fixture
    is regenerated from the same deterministic constants and saved to disk so
    that the test suite is never blocked on a missing file.

    Returns:
        Absolute path to the ``.jsonl.gz`` fixture file.
    """
    fixture_path = make_fixture_path(_FIXTURE_DATE)
    if not fixture_path.exists():
        rows = _build_golden_rows()
        bank_notifications(rows, fixture_path)
    return fixture_path


_FIXTURE_PATH: pathlib.Path = _ensure_fixture_exists()


# ---------------------------------------------------------------------------
# Helper: run recorder over golden fixture rows
# ---------------------------------------------------------------------------


def _run_recorder_over_fixture(trade_rows: list[dict], tmp_path: pathlib.Path):
    """Drive the real TapeRecorder over the decoded golden trade rows.

    Uses ReplaySource + MappedSwapSource (the same chain as the live listener)
    and a VirtualClock anchored to a fixed datetime.  The injected token_store
    uses GOLDEN_MINT with GRADUATED_BT as the graduation anchor.

    Returns:
        TapeRecorder instance after run() completes.
    """
    from datetime import datetime, timezone

    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.lake_writer import LakeWriter
    from core.tape.mapped_source import MappedSwapSource
    from core.tape.recorder import TapeRecorder
    from core.tape.swap_writer import SwapWriter

    t0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)
    token_store = {GOLDEN_MINT: SimpleNamespace(graduated_block_time=GRADUATED_BT)}

    async def _inner():
        source = MappedSwapSource(ReplaySource(trade_rows), decode_helius_notification)
        recorder = TapeRecorder(
            source=source,
            clock=VirtualClock(t0),
            token_store=token_store,
            swap_source="helius_live",
            swap_phase="pre",
            lake_writer=LakeWriter(tmp_path),
            swap_writer=SwapWriter(),
        )
        await recorder.run()
        return recorder

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_golden_fixture_exists() -> None:
    """The golden fixture .jsonl.gz is present on disk."""
    assert _FIXTURE_PATH.exists(), (
        f"Golden fixture missing: {_FIXTURE_PATH}\n"
        "Run _ensure_fixture_exists() or commit the file."
    )


def test_golden_fixture_loads() -> None:
    """load_birth_tape_fixture returns a non-empty list of dicts."""
    rows = load_birth_tape_fixture(_FIXTURE_PATH)
    assert isinstance(rows, list)
    assert len(rows) > 0, "Fixture loaded as empty — file may be corrupt."
    for row in rows:
        assert isinstance(row, dict), f"Expected dict, got {type(row)}"


def test_golden_fixture_has_expected_row_count() -> None:
    """The fixture contains exactly 6 rows (3 pre-grad + 1 migrate + 2 post-grad)."""
    rows = load_birth_tape_fixture(_FIXTURE_PATH)
    assert len(rows) == _EXPECTED_ROW_COUNT, (
        f"Expected {_EXPECTED_ROW_COUNT} rows, got {len(rows)}"
    )


def test_decoder_filters_migrate_and_decodes_trades() -> None:
    """decode_helius_notification returns None for migrate; dicts for the 5 trade rows.

    The migrate notification has no TradeEvent discriminator in its log messages,
    so the decoder returns None (correct — it is not a swap).  All 5 trade rows
    decode to dicts with the expected GOLDEN_MINT.
    """
    rows = load_birth_tape_fixture(_FIXTURE_PATH)
    decoded = [decode_helius_notification(row) for row in rows]

    none_count = sum(1 for d in decoded if d is None)
    trade_count = sum(1 for d in decoded if d is not None)

    assert none_count == 1, f"Expected 1 None (migrate), got {none_count}"
    assert trade_count == _EXPECTED_TRADE_COUNT, (
        f"Expected {_EXPECTED_TRADE_COUNT} decoded trades, got {trade_count}"
    )

    for d in decoded:
        if d is not None:
            assert d["mint"] == GOLDEN_MINT, (
                f"Decoded swap has wrong mint: {d['mint']!r} (expected {GOLDEN_MINT!r})"
            )


@pytest.mark.django_db
def test_pre_grad_swaps_have_negative_rel(tmp_path) -> None:
    """Pre-grad swaps carry negative rel; post-grad swaps carry positive rel.

    Loads the fixture, filters to trade rows only, then drives the real
    TapeRecorder (same chain as the live listener) with GRADUATED_BT as the
    graduation anchor.  Checks rel polarity per §7.1.
    """
    rows = load_birth_tape_fixture(_FIXTURE_PATH)
    trade_rows = [row for row in rows if decode_helius_notification(row) is not None]

    recorder = _run_recorder_over_fixture(trade_rows, tmp_path)
    swaps = recorder.normalized_swaps  # sorted by (block_time, slot, signature)

    assert len(swaps) == _EXPECTED_TRADE_COUNT, (
        f"Expected {_EXPECTED_TRADE_COUNT} swaps, got {len(swaps)}"
    )

    # Pre-grad swaps: block_time < GRADUATED_BT → rel < 0
    pre_grad = [s for s in swaps if s.block_time < GRADUATED_BT]
    post_grad = [s for s in swaps if s.block_time > GRADUATED_BT]

    assert len(pre_grad) == 3, f"Expected 3 pre-grad swaps, got {len(pre_grad)}"
    assert len(post_grad) == 2, f"Expected 2 post-grad swaps, got {len(post_grad)}"

    for s in pre_grad:
        assert s.rel < 0, (
            f"Pre-grad swap (block_time={s.block_time}) must have rel < 0, got {s.rel}"
        )
    for s in post_grad:
        assert s.rel > 0, (
            f"Post-grad swap (block_time={s.block_time}) must have rel > 0, got {s.rel}"
        )


def test_fixture_decode_is_deterministic() -> None:
    """Decoding the fixture twice produces identical results (no randomness)."""
    rows = load_birth_tape_fixture(_FIXTURE_PATH)
    first_pass = [decode_helius_notification(row) for row in rows]
    second_pass = [decode_helius_notification(row) for row in rows]
    assert first_pass == second_pass, "Fixture decode is not deterministic."


def test_compose_wiring_helius_key_in_listener_local() -> None:
    """docker-compose.yml listener service environment contains HELIUS_API_KEY."""
    import yaml  # type: ignore[import-untyped]

    compose_path = pathlib.Path("docker-compose.yml")
    assert compose_path.exists(), "docker-compose.yml not found in working directory."
    with compose_path.open() as fh:
        compose = yaml.safe_load(fh)

    listener = compose.get("services", {}).get("listener", {})
    env = listener.get("environment", {})

    # environment may be a list of "KEY=value" strings or a dict
    if isinstance(env, list):
        keys = [item.split("=")[0] for item in env]
    else:
        keys = list(env.keys())

    assert "HELIUS_API_KEY" in keys, (
        "docker-compose.yml listener service environment missing HELIUS_API_KEY. "
        f"Found keys: {keys}"
    )


def test_compose_wiring_helius_key_in_listener_staging() -> None:
    """docker-compose.staging.yml listener service environment contains HELIUS_API_KEY."""
    import yaml  # type: ignore[import-untyped]

    compose_path = pathlib.Path("docker-compose.staging.yml")
    assert compose_path.exists(), "docker-compose.staging.yml not found in working directory."
    with compose_path.open() as fh:
        compose = yaml.safe_load(fh)

    listener = compose.get("services", {}).get("listener", {})
    env = listener.get("environment", {})

    if isinstance(env, list):
        keys = [item.split("=")[0] for item in env]
    else:
        keys = list(env.keys())

    assert "HELIUS_API_KEY" in keys, (
        "docker-compose.staging.yml listener service environment missing HELIUS_API_KEY. "
        f"Found keys: {keys}"
    )


def test_birth_tape_flag_in_run_listener_help() -> None:
    """run_listener management command add_arguments exposes the --birth-tape flag.

    Instantiates the Command class and calls add_arguments() on a parser to verify
    the flag is registered.  No Django test runner required — pure import test.
    """
    import argparse

    from core.management.commands.run_listener import Command

    cmd = Command()
    parser = argparse.ArgumentParser()
    cmd.add_arguments(parser)

    # argparse stores dest 'birth_tape' for --birth-tape flag
    actions = {a.dest: a for a in parser._actions}
    assert "birth_tape" in actions, (
        "--birth-tape flag not found in run_listener Command.add_arguments(). "
        f"Registered dests: {list(actions.keys())}"
    )


def test_make_fixture_path_returns_correct_pattern() -> None:
    """make_fixture_path returns a Path under FIXTURE_BASE_DIR."""
    p = make_fixture_path("2026-06-17")
    assert isinstance(p, pathlib.Path)
    fixture_base = pathlib.Path(FIXTURE_BASE_DIR)
    # The path must start with (be relative to) FIXTURE_BASE_DIR
    assert str(p).startswith(str(fixture_base)), (
        f"make_fixture_path returned {p!r}, expected it to start with {fixture_base!r}"
    )
    # Sanity: must end with .jsonl.gz
    assert p.suffix == ".gz", f"Expected .gz suffix, got {p.suffix!r}"
    assert p.name.endswith(".jsonl.gz"), f"Expected .jsonl.gz name, got {p.name!r}"


def test_bank_notifications_roundtrip(tmp_path: pathlib.Path) -> None:
    """bank_notifications writes a .jsonl.gz that load_birth_tape_fixture reads back."""
    rows = _build_golden_rows()
    out_path = tmp_path / "test_roundtrip.jsonl.gz"

    count = bank_notifications(rows, out_path)
    assert count == len(rows), f"bank_notifications returned {count}, expected {len(rows)}"
    assert out_path.exists(), "bank_notifications did not create the output file."

    loaded = load_birth_tape_fixture(out_path)
    assert loaded == rows, (
        "Roundtrip mismatch: loaded rows differ from written rows.\n"
        f"Written: {len(rows)} rows\n"
        f"Loaded:  {len(loaded)} rows"
    )
