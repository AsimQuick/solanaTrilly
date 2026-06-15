# ---
# module: tools.firehose_activate
# sprint: sprint-5
# story: US-22 AC-22.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: asyncio, gzip, json, argparse, os, ssl, sys, time, pathlib, certifi, websockets
# ---
"""firehose_activate.py — the SINGLE deliberate, time-boxed Birdeye SUBSCRIBE_TXS activation (PRD §15.7).

Each invocation consumes ONE firehose activation from the project's 10-activation
Birdeye budget.  It opens the live Birdeye WebSocket, subscribes to SUBSCRIBE_TXS
for a graduated pump.fun token, captures the token's REAL PumpSwap swap tape, and
banks it as a durable, immutable golden fixture under ``lake/golden/`` (raw =
immutable truth, §6.4.1).  The spend compounds into the offline replay corpus
(PRD §11) — after which all further dev replays offline forever.

There is NO synthetic fallback: an activation banks REALITY or it banks nothing.
A budget item is too expensive to spend on fabricated rows (the §15.7 HARD RULE).

Banked fixture schema is the EXACT raw Birdeye SUBSCRIBE_TXS payload as received
(the inner ``data`` object of each ``TXS_DATA`` envelope), one JSON object per
gzipped line.  Field names are Birdeye-native (``blockUnixTime``, ``txHash``,
``tokenAddress``, ``tokenPrice``, ``volumeUSD`` …) so the fixture is the
re-derivable immutable truth, never a lossy normalization.

Usage:
    python tools/firehose_activate.py --mint <addr> [--duration-seconds N] [--max-events N]

    --mint              Token mint to subscribe (a graduated pump.fun token, mint
                        usually ends in "pump"). REQUIRED — no auto-discovery of a
                        random high-volume token (that banked WSOL noise last time).
    --duration-seconds  Hard time-box for the capture window (default 300, max 1800
                        = the §15.7 30-minute ceiling).
    --max-events        Stop early once this many PumpSwap swaps are banked
                        (default 40 — enough for a durable tape, frugal on credits).
    --api-key           Birdeye API key. Falls back to BIRDEYE_API_KEY env var.

Output:
    lake/golden/birdeye_subscribe_txs/dt=YYYY-MM-DD/<MINT>_pumpswap_golden.jsonl.gz
"""
import argparse
import asyncio
import gzip
import json
import os
import ssl
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BIRDEYE_WS_URL = "wss://public-api.birdeye.so/socket/solana"
BIRDEYE_WS_ORIGIN = "ws://public-api.birdeye.so"
BIRDEYE_WS_SUBPROTOCOL = "echo-protocol"

# PumpSwap AMM — the sole graduation destination (CLAUDE.md / PRD).  A swap is a
# PumpSwap swap when Birdeye tags its source "pump_amm" or its on-chain program
# is the PumpSwap program id.
PUMPSWAP_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
PUMPSWAP_SOURCE = "pump_amm"

# §15.7 time-box ceiling — 30 minutes.
MAX_DURATION_SECONDS = 1800

LAKE_BASE = Path(__file__).resolve().parent.parent / "lake" / "golden" / "birdeye_subscribe_txs"

# The raw Birdeye SUBSCRIBE_TXS swap payload (§3.3) carries at least these keys.
# Used to validate a captured event is a real, complete swap before banking.
REQUIRED_RAW_FIELDS = frozenset(
    {
        "blockUnixTime",
        "owner",
        "source",
        "txHash",
        "side",
        "tokenAddress",
        "pricePair",
        "volumeUSD",
        "tokenPrice",
        "blockNumber",
    }
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no network)
# ---------------------------------------------------------------------------


def is_pumpswap_swap(event: dict) -> bool:
    """True if *event* is a PumpSwap swap (graduation destination, §3 / CLAUDE.md)."""
    if not isinstance(event, dict):
        return False
    if event.get("source") == PUMPSWAP_SOURCE:
        return True
    return event.get("interactedProgramId") == PUMPSWAP_PROGRAM


def missing_required_fields(event: dict) -> set[str]:
    """Return the set of REQUIRED_RAW_FIELDS absent from *event* (empty == complete)."""
    if not isinstance(event, dict):
        return set(REQUIRED_RAW_FIELDS)
    return {f for f in REQUIRED_RAW_FIELDS if f not in event}


def is_bankable_swap(event: dict, mint: str) -> bool:
    """True if *event* is a real, complete, landed PumpSwap swap for *mint* worth banking.

    Guards the §15.7 HARD RULE: only genuine, schema-complete, landed reality is banked.
    Failed transactions (``event['failed'] is True``) are excluded — landed-only (§6.2).
    """
    if not is_pumpswap_swap(event):
        return False
    if missing_required_fields(event):
        return False
    if event.get("failed") is True:
        return False
    return event.get("tokenAddress") == mint


def unwrap_envelope(parsed: dict) -> dict | None:
    """Unwrap a Birdeye ``{"type":"TXS_DATA","data":{…swap…}}`` envelope.

    Returns the inner swap dict for TXS_DATA messages, else None (WELCOME,
    ERROR, non-dict, …).
    """
    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") != "TXS_DATA":
        return None
    data = parsed.get("data")
    return data if isinstance(data, dict) else None


def bank_fixture(mint: str, events: list[dict], *, date: str, base: Path = LAKE_BASE) -> Path:
    """Write *events* to the canonical golden fixture path and return it.

    Append-only, gzip-compressed jsonl; one raw event per line, in capture order
    (raw = immutable truth, §6.4.1).
    """
    dest_dir = base / f"dt={date}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = dest_dir / f"{mint}_pumpswap_golden.jsonl.gz"
    with gzip.open(fixture_path, "wb") as gz:
        for event in events:
            gz.write((json.dumps(event, sort_keys=True) + "\n").encode("utf-8"))
    return fixture_path


def read_fixture(path: str | Path) -> list[dict]:
    """Read a banked gzip jsonl fixture back into a list of dicts (verification helper)."""
    rows: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _process_message_stream(
    message_jsons: list[str],
    mint: str,
    max_events: int,
    deadline: float,
    _monotonic=time.monotonic,
) -> list[dict]:
    """Filter, unwrap, and de-dup a list of raw WS message JSON strings.

    This is the pure, unit-testable processing kernel for CI.  The live
    ``capture_pumpswap_tape`` uses the same helpers (``unwrap_envelope``,
    ``is_bankable_swap``) — one code path, no divergence between offline
    tests and live activation.

    Stops when the monotonic clock reaches *deadline* (§15.7 time-box guard)
    or when *max_events* bankable swaps have been collected.  Only
    ``source=pump_amm``, schema-complete, non-failed, correct-mint events
    are kept; duplicates are de-duped on ``txHash``.
    """
    events: list[dict] = []
    seen_sigs: set[str] = set()
    for raw in message_jsons:
        if _monotonic() >= deadline:
            break
        if len(events) >= max_events:
            break
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        event = unwrap_envelope(parsed)
        if event is None or not is_bankable_swap(event, mint):
            continue
        sig = event.get("txHash")
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Live capture (network — not unit-tested; exercised live during the activation)
# ---------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:  # pragma: no cover - network/runtime only
    """Build an SSL context with a real CA bundle (macOS python lacks system certs)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


async def capture_pumpswap_tape(  # pragma: no cover - live WebSocket, exercised during activation
    api_key: str,
    mint: str,
    *,
    duration_seconds: int,
    max_events: int,
) -> list[dict]:
    """Subscribe to SUBSCRIBE_TXS for *mint*; collect real PumpSwap swaps.

    Time-boxed to *duration_seconds* (§15.7); stops early at *max_events*.
    Returns the raw inner swap dicts (Birdeye-native fields), de-duplicated on
    txHash so a multi-leg route does not double-bank the same signature.
    """
    import websockets  # noqa: PLC0415 — lazy import keeps module import-time clean

    events: list[dict] = []
    seen_sigs: set[str] = set()
    deadline = time.monotonic() + duration_seconds
    url = f"{BIRDEYE_WS_URL}?x-api-key={api_key}"

    print(f"[capture] Connecting to {BIRDEYE_WS_URL} …")
    async with websockets.connect(
        url,
        extra_headers={"Origin": BIRDEYE_WS_ORIGIN},
        subprotocols=[BIRDEYE_WS_SUBPROTOCOL],
        ssl=_ssl_context(),
        open_timeout=20,
        ping_interval=20,
        ping_timeout=10,
    ) as ws:
        await ws.send(
            json.dumps(
                {"type": "SUBSCRIBE_TXS", "data": {"queryType": "simple", "address": mint}}
            )
        )
        print(f"[capture] Subscribed mint={mint[:16]}… window={duration_seconds}s target={max_events}")

        while time.monotonic() < deadline and len(events) < max_events:
            remaining = deadline - time.monotonic()
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 10.0))
            except asyncio.TimeoutError:
                elapsed = duration_seconds - (deadline - time.monotonic())
                print(f"[capture] … {elapsed:.0f}s elapsed, {len(events)} PumpSwap swaps so far")
                continue
            except Exception as exc:  # noqa: BLE001 — log + stop on any WS error
                print(f"[capture] recv error: {exc}")
                break

            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            event = unwrap_envelope(parsed)
            if event is None or not is_bankable_swap(event, mint):
                continue

            sig = event.get("txHash")
            if sig in seen_sigs:
                continue
            seen_sigs.add(sig)
            events.append(event)
            if len(events) % 5 == 0:
                print(f"[capture] {len(events)} PumpSwap swaps captured …")

    return events


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Birdeye SUBSCRIBE_TXS firehose activation (§15.7)")
    parser.add_argument("--mint", required=True, help="Graduated pump.fun token mint to subscribe")
    parser.add_argument("--duration-seconds", type=int, default=300, help="Capture time-box (default 300, max 1800)")
    parser.add_argument("--max-events", type=int, default=40, help="Stop early after N PumpSwap swaps (default 40)")
    parser.add_argument("--api-key", default=None, help="Birdeye API key (falls back to BIRDEYE_API_KEY)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    api_key = args.api_key or os.environ.get("BIRDEYE_API_KEY", "")
    if not api_key:
        print("ERROR: No Birdeye API key. Set BIRDEYE_API_KEY or pass --api-key.")
        return 1

    duration = min(args.duration_seconds, MAX_DURATION_SECONDS)
    date = time.strftime("%Y-%m-%d", time.gmtime())

    print(f"\n[activate] Mint:     {args.mint}")
    print(f"[activate] Window:   {duration}s (§15.7 ceiling {MAX_DURATION_SECONDS}s)")
    print("[activate] Budget:   10 → 9 Birdeye activations after this run")
    print(f"[activate] Fixture:  lake/golden/birdeye_subscribe_txs/dt={date}/\n")

    t_start = time.monotonic()
    events = asyncio.run(
        capture_pumpswap_tape(api_key, args.mint, duration_seconds=duration, max_events=args.max_events)
    )
    elapsed = time.monotonic() - t_start

    if not events:
        print(
            f"\n[activate] 0 PumpSwap swaps captured in {elapsed:.1f}s. "
            "Banking NOTHING (no synthetic fallback — §15.7 HARD RULE). "
            "Re-run against a more active graduated token."
        )
        return 2

    fixture_path = bank_fixture(args.mint, events, date=date)
    try:
        rel_path = fixture_path.relative_to(Path(__file__).resolve().parent.parent)
    except ValueError:
        rel_path = fixture_path
    duration_str = f"{elapsed / 60:.1f} min"

    print(f"\n[activate] Banked {len(events)} real PumpSwap swaps → {fixture_path}")
    print("\n--- ACTIVATION SUMMARY ---")
    print(f"Date:             {date}")
    print(f"Mint:             {args.mint}")
    print(f"Events:           {len(events)} (live PumpSwap swaps)")
    print(f"Duration:         {duration_str}")
    print(f"Fixture:          {rel_path}")
    print("Budget remaining: 9 Birdeye / 10 Helius")
    print("\n--- LOG TABLE ROW ---")
    print(
        f"| {date} | dev-team | Birdeye SUBSCRIBE_TXS | "
        f"Bank first golden PumpSwap tape for offline replay/parity (AC-22.2, D4) | "
        f"{duration_str} | 9 Birdeye / 10 Helius | "
        f"{rel_path} ({len(events)} swaps) |"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
