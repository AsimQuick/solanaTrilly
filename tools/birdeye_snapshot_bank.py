# ---
# module: tools.birdeye_snapshot_bank
# sprint: sprint-8
# story: US-37 AC-37.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.birdeye_snapshot_source, core.encoders, json, pathlib
# ---
"""birdeye_snapshot_bank.py — bank one on-demand Birdeye REST snapshot as a durable fixture.

Calls the three Birdeye Professional REST endpoints (token_security, v3/token/holder,
token_overview) for a single mint, assembles the canonical seven-field snapshot dict
via BirdeyeSnapshotSource, writes the result as a JSON fixture via JsonSafeEncoder,
and records the banking context (mint, banked_at, elapsed_s, as_of).

Budget: one on-demand REST read within the Birdeye professional allowance.
NOT a firehose activation — no budget line is spent (PRD §15.7, AC-37.2).

The live fetch_and_bank() and main() functions are marked ``# pragma: no cover``
because they perform real network I/O.  All other functions are pure helpers,
fully covered by the offline CI gate (test_birdeye_snapshot_fixture_ac372.py).

Pure helper exports (imported by tests):
    FIXTURE_BASE_DIR        -- relative path root for banked fixtures
    SNAPSHOT_FIXTURE_MINT   -- deterministic test mint for the CI golden fixture
    make_fixture_path()     -- deterministic output path for a given date string
    bank_snapshot()         -- write fixture dict → JSON file via JsonSafeEncoder
    load_snapshot_fixture() -- read the JSON fixture back to a dict
    build_fixture_data()    -- construct fixture envelope with banking metadata
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

#: Root directory for banked Birdeye snapshot fixtures (relative to repo root)
FIXTURE_BASE_DIR: str = "lake/golden/birdeye_snapshot"

#: Deterministic test mint used for the CI golden fixture (not a live address)
SNAPSHOT_FIXTURE_MINT: str = "AC372BirdeyeSnapshotMint1111111111111111111"

# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def make_fixture_path(date_str: str) -> pathlib.Path:
    """Return the output JSON path for a given ISO date string.

    The path is deterministic: ``<FIXTURE_BASE_DIR>/dt=<date_str>/
    birdeye_snapshot_golden.json``.

    Args:
        date_str: ISO-8601 date string, e.g. ``"2026-06-17"``.

    Returns:
        A :class:`pathlib.Path` (not yet created on disk).
    """
    return (
        pathlib.Path(FIXTURE_BASE_DIR) / f"dt={date_str}" / "birdeye_snapshot_golden.json"
    )


def build_fixture_data(
    mint: str,
    raw: dict[str, Any],
    as_of: datetime,
    elapsed_s: int,
    banked_at: datetime,
) -> dict[str, Any]:
    """Construct the fixture envelope dict containing banking metadata + raw payload.

    Args:
        mint:       Solana mint address used for the snapshot.
        raw:        Seven-field snapshot dict from BirdeyeSnapshotSource.get_snapshot().
        as_of:      The as_of datetime forwarded to BirdeyeSnapshotSource.
        elapsed_s:  Seconds since the token's graduation at snapshot time.
        banked_at:  Wall-clock time when this fixture was banked.

    Returns:
        A fixture envelope dict suitable for bank_snapshot().
    """
    return {
        "banked_at": banked_at.isoformat(),
        "mint": mint,
        "elapsed_s": elapsed_s,
        "as_of": as_of.isoformat(),
        "raw": raw,
    }


def bank_snapshot(fixture_data: dict[str, Any], out_path: pathlib.Path) -> None:
    """Write a fixture envelope dict to a JSON file via JsonSafeEncoder.

    raw = immutable truth: the file is written once and never mutated.
    JsonSafeEncoder ensures NaN/Inf/Decimal values are stored as null/float
    so the fixture is always spec-valid JSON (H3/US-5 guard).

    Args:
        fixture_data: Fixture envelope dict from build_fixture_data().
        out_path:     Destination path (parent directory is created if needed).
    """
    import sys

    # Resolve core.encoders relative to the repo root (tools/ sits beside core/)
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from core.encoders import JsonSafeEncoder

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(fixture_data, fh, cls=JsonSafeEncoder, indent=2)
        fh.write("\n")


def load_snapshot_fixture(path: pathlib.Path) -> dict[str, Any]:
    """Read a banked JSON fixture file back to a dict.

    Args:
        path: Path to the JSON fixture file written by bank_snapshot().

    Returns:
        The fixture envelope dict, including ``raw``, ``mint``, ``elapsed_s``,
        ``as_of``, and ``banked_at``.

    Raises:
        FileNotFoundError: if the fixture file does not exist.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Live activation (real network I/O — not exercised by CI)
# ---------------------------------------------------------------------------


def fetch_and_bank(  # pragma: no cover
    api_key: str,
    mint: str,
    elapsed_s: int,
    out_path: pathlib.Path,
) -> dict[str, Any]:
    """Fetch one on-demand Birdeye snapshot and bank it as a durable JSON fixture.

    This function performs REAL Birdeye REST calls (three endpoints: security,
    holder, overview) and writes the assembled seven-field result to out_path
    via JsonSafeEncoder.  It consumes one on-demand read from the Birdeye
    Professional allowance — NOT a firehose activation (PRD §15.7).

    Call once per fixture refresh.  Commit the resulting JSON file so the CI
    gate runs offline forever against the real banked snapshot.

    Args:
        api_key:   Birdeye Professional API key (X-API-KEY header).
        mint:      Solana mint address to snapshot.
        elapsed_s: Seconds since token graduation (recorded in fixture metadata).
        out_path:  Destination path for the JSON fixture file.

    Returns:
        The fixture envelope dict that was written to disk.
    """
    import sys
    from datetime import timezone

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from core.tape.birdeye_snapshot_source import BirdeyeSnapshotSource

    source = BirdeyeSnapshotSource(api_key=api_key)
    as_of = datetime.now(tz=timezone.utc)
    raw = source.get_snapshot(mint, as_of=as_of)
    banked_at = datetime.now(tz=timezone.utc)

    fixture_data = build_fixture_data(
        mint=mint,
        raw=raw,
        as_of=as_of,
        elapsed_s=elapsed_s,
        banked_at=banked_at,
    )
    bank_snapshot(fixture_data, out_path)
    return fixture_data


def main() -> None:  # pragma: no cover
    """CLI entry point: fetch and bank one Birdeye snapshot.

    Usage::

        python tools/birdeye_snapshot_bank.py \\
            --api-key <BIRDEYE_API_KEY> \\
            --mint <MINT_ADDRESS> \\
            --elapsed-s 60 \\
            --date 2026-06-17

    The fixture is written to:
        lake/golden/birdeye_snapshot/dt=<date>/birdeye_snapshot_golden.json
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Bank one on-demand Birdeye REST snapshot as a durable fixture."
    )
    parser.add_argument("--api-key", required=True, help="Birdeye Professional API key")
    parser.add_argument("--mint", required=True, help="Solana mint address to snapshot")
    parser.add_argument(
        "--elapsed-s",
        type=int,
        default=60,
        help="Seconds since token graduation (default: 60)",
    )
    parser.add_argument(
        "--date",
        default="2026-06-17",
        help="ISO date string for fixture path (default: 2026-06-17)",
    )
    args = parser.parse_args()

    out_path = make_fixture_path(args.date)
    print(f"Banking snapshot for mint={args.mint} to {out_path} ...")
    fixture = fetch_and_bank(
        api_key=args.api_key,
        mint=args.mint,
        elapsed_s=args.elapsed_s,
        out_path=out_path,
    )
    print(f"Banked. liquidity={fixture['raw']['liquidity']}, lp_burned={fixture['raw']['lp_burned']}")
    print(f"Fixture written to {out_path}")


if __name__ == "__main__":  # pragma: no cover
    main()
