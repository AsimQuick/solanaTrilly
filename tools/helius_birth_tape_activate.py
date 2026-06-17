# ---
# module: tools.helius_birth_tape_activate
# sprint: sprint-8
# story: US-34 AC-34.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: argparse, base64, gzip, hashlib, json, pathlib, struct, sys
# ---
"""helius_birth_tape_activate.py — ONE deliberate, time-boxed Helius birth-tape activation.

Subscribes program-wide to the pump.fun bonding-curve program via Helius
transactionSubscribe, captures pre-grad → graduation → post-grad raw notifications
for a token that graduates within the capture window, and banks the result as a
durable .jsonl.gz fixture.

Budget: Helius 9 → 8 when the live window is opened.

The live connect_and_capture() and main() functions are marked ``# pragma: no cover``
because they perform real network I/O.  All other functions are pure helpers,
fully covered by the offline CI gate (test_birth_tape_live_fixture_ac343.py).

Pure helper exports (imported by tests):
    FIXTURE_BASE_DIR      -- relative path root for banked fixtures
    PUMP_FUN_PROGRAM      -- pump.fun bonding-curve program address
    make_fixture_path()   -- deterministic output path for a given date string
    bank_notifications()  -- write list[dict] → .jsonl.gz, return count
    load_birth_tape_fixture() -- read .jsonl.gz back to list[dict]
"""
from __future__ import annotations

import gzip
import json
import pathlib
from typing import Any

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Root directory for banked birth-tape fixtures (relative to repo root)
FIXTURE_BASE_DIR: str = "lake/golden/helius_birth_tape"

#: pump.fun bonding-curve program address (the transactionSubscribe target)
PUMP_FUN_PROGRAM: str = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"

#: Helius Atlas mainnet WebSocket endpoint
_HELIUS_WS_URL: str = "wss://atlas-mainnet.helius-rpc.com"

#: Default capture window in seconds (time-boxed, PRD §15.7)
_DEFAULT_DURATION_SECONDS: int = 1200  # 20-min box

#: Maximum capture window enforced by this tool
_MAX_DURATION_SECONDS: int = 1800  # 30-min hard cap

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def make_fixture_path(date_str: str) -> pathlib.Path:
    """Return the output .jsonl.gz path for a given ISO date string.

    The path is deterministic: ``<FIXTURE_BASE_DIR>/dt=<date_str>/
    helius_birth_tape_pregrad_golden.jsonl.gz``.

    Args:
        date_str: ISO-8601 date string, e.g. ``"2026-06-17"``.

    Returns:
        A :class:`pathlib.Path` (not yet created on disk).
    """
    return pathlib.Path(FIXTURE_BASE_DIR) / f"dt={date_str}" / "helius_birth_tape_pregrad_golden.jsonl.gz"


def bank_notifications(notifications: list[dict[str, Any]], out_path: pathlib.Path) -> int:
    """Write a list of raw Helius notification dicts to a .jsonl.gz fixture file.

    Each dict is serialised as one JSON line in the gzip-compressed file.
    The parent directory is created if it does not exist.

    Args:
        notifications: List of raw notification dicts (one per line in output).
        out_path:       Destination path (should end in ``.jsonl.gz``).

    Returns:
        Number of notifications written (== ``len(notifications)``).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out_path, "wt", encoding="utf-8") as fh:
        for notification in notifications:
            fh.write(json.dumps(notification) + "\n")
    return len(notifications)


def load_birth_tape_fixture(fixture_path: pathlib.Path) -> list[dict[str, Any]]:
    """Load a .jsonl.gz birth-tape fixture file and return its rows as dicts.

    Args:
        fixture_path: Path to a ``.jsonl.gz`` file written by :func:`bank_notifications`.

    Returns:
        List of dicts, one per line in the file.

    Raises:
        FileNotFoundError: If *fixture_path* does not exist.
    """
    rows: list[dict[str, Any]] = []
    with gzip.open(fixture_path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Live network path — pragma: no cover
# ---------------------------------------------------------------------------


async def connect_and_capture(  # pragma: no cover
    api_key: str,
    duration_seconds: int = _DEFAULT_DURATION_SECONDS,
) -> list[dict[str, Any]]:
    """Open a Helius transactionSubscribe WebSocket and capture raw notifications.

    This is the ONE deliberate, time-boxed live-window function.  It subscribes
    program-wide to ``PUMP_FUN_PROGRAM`` and collects every transactionNotification
    frame until either the time box expires or the caller cancels.

    Args:
        api_key:          Helius API key (passed as a URL query parameter).
        duration_seconds: Capture window length in seconds.
                          Clamped to ``_MAX_DURATION_SECONDS``.

    Returns:
        List of raw transactionNotification dicts captured during the window.
    """
    import asyncio

    import websockets  # type: ignore[import-untyped]  # lazy import — live path only

    duration_seconds = min(duration_seconds, _MAX_DURATION_SECONDS)
    url = f"{_HELIUS_WS_URL}/?api-key={api_key}"
    collected: list[dict[str, Any]] = []

    subscribe_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "transactionSubscribe",
            "params": [
                {"accountInclude": [PUMP_FUN_PROGRAM]},
                {
                    "commitment": "confirmed",
                    "encoding": "jsonParsed",
                    "transactionDetails": "full",
                    "maxSupportedTransactionVersion": 0,
                },
            ],
        }
    )

    async with websockets.connect(url, open_timeout=20, ping_interval=20, ping_timeout=10) as ws:
        await ws.send(subscribe_msg)
        # Consume ack
        try:
            await ws.recv()
        except Exception:
            pass

        deadline = asyncio.get_event_loop().time() + duration_seconds
        async for raw_message in ws:
            try:
                parsed = json.loads(raw_message)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and parsed.get("method") == "transactionNotification":
                collected.append(parsed)
            if asyncio.get_event_loop().time() >= deadline:
                break

    return collected


def main() -> None:  # pragma: no cover
    """CLI entry point for the deliberate birth-tape activation.

    Usage::

        python tools/helius_birth_tape_activate.py \\
            --api-key $HELIUS_API_KEY \\
            --duration 1200 \\
            --date 2026-06-17

    The captured notifications are written to::

        lake/golden/helius_birth_tape/dt=<DATE>/helius_birth_tape_pregrad_golden.jsonl.gz

    This activation MUST be logged in ``ops/firehose_activation_log.md`` per the
    HARD RULE before merge (budget: Helius 9 → 8).
    """
    import argparse
    import asyncio
    import sys

    parser = argparse.ArgumentParser(
        description="ONE deliberate Helius birth-tape transactionSubscribe activation (PRD §15.7)."
    )
    parser.add_argument("--api-key", required=True, help="Helius API key")
    parser.add_argument(
        "--duration",
        type=int,
        default=_DEFAULT_DURATION_SECONDS,
        help=f"Capture window in seconds (max {_MAX_DURATION_SECONDS})",
    )
    parser.add_argument("--date", required=True, help="ISO date for fixture path, e.g. 2026-06-17")
    args = parser.parse_args()

    print(f"[birth-tape-activate] Opening Helius transactionSubscribe window: {args.duration}s")
    notifications = asyncio.run(
        connect_and_capture(api_key=args.api_key, duration_seconds=args.duration)
    )
    print(f"[birth-tape-activate] Captured {len(notifications)} notifications")

    if not notifications:
        print("[birth-tape-activate] No notifications captured — aborting (no fixture written).")
        sys.exit(1)

    out_path = make_fixture_path(args.date)
    count = bank_notifications(notifications, out_path)
    print(f"[birth-tape-activate] Banked {count} notifications → {out_path}")
    print("[birth-tape-activate] LOG THIS ACTIVATION in ops/firehose_activation_log.md (Helius 9→8).")


if __name__ == "__main__":  # pragma: no cover
    main()
