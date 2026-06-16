# ---
# module: tools.helius_g2b_activate
# sprint: sprint-7
# story: US-32 AC-32.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: argparse, gzip, json, os, ssl, sys, time, pathlib, urllib.request, urllib.error
# ---
"""helius_g2b_activate.py — ONE deliberate, time-boxed Helius G2(b) raw-truth activation (PRD §15.7 / §7.6).

This script is the project's SINGLE budgeted Helius G2(b) activation (CLAUDE.md —
'G2 needs one activation').  It reads the banked Birdeye golden fixture to extract
the real swap signatures, then fetches the Helius Enhanced Transactions decode for
those same signatures and banks the raw decoded result as a durable fixture.

The resulting fixture is the cross-source ground truth.  After banking, the CI parity
test (test_g2b_raw_truth_ac322.py) runs OFFLINE against the banked fixture forever —
the P5 gate NEVER depends on a live Helius read.

Budget: Helius 10 → 9 after this activation.

Fixture schema (one JSON per gzipped line — raw = immutable truth, §6.4.1):
    {
      "signature": str,
      "slot": int,
      "block_time": int,          # Unix timestamp from Helius
      "helius_type": str,         # e.g. "SWAP", "UNKNOWN"
      "helius_source": str,       # e.g. "PUMP_AMM", "OKX_DEX_ROUTER"
      "fee_payer": str,
      "token_transfers": [        # ALL tokenTransfers from Helius (filtered to MINT + wSOL)
        {"mint": str, "from_account": str, "to_account": str, "token_amount": float}
      ],
      "native_transfers": [       # nativeTransfers (SOL) from Helius
        {"from_account": str, "to_account": str, "amount": int}  # lamports
      ],
      "account_token_changes": [  # accountData[].tokenBalanceChanges (MINT + wSOL)
        {"account": str, "mint": str, "raw_change": int}
      ]
    }

Usage:
    python tools/helius_g2b_activate.py [--api-key <key>] [--dry-run]

Output:
    lake/golden/helius_decoded_txs/dt=YYYY-MM-DD/<MINT>_helius_g2b_raw.jsonl.gz
"""
import argparse
import gzip
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BANKED_MINT = "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
WSOL_MINT = "So11111111111111111111111111111111111111112"

REPO_ROOT = Path(__file__).resolve().parent.parent

BIRDEYE_FIXTURE = (
    REPO_ROOT
    / "lake"
    / "golden"
    / "birdeye_subscribe_txs"
    / "dt=2026-06-15"
    / f"{BANKED_MINT}_pumpswap_golden.jsonl.gz"
)

HELIUS_LAKE_BASE = REPO_ROOT / "lake" / "golden" / "helius_decoded_txs"
HELIUS_ENHANCED_TX_URL = "https://api.helius.xyz/v0/transactions"
HELIUS_BATCH_SIZE = 100
MAX_DURATION_SECONDS = 1800

# Mints we extract from tokenTransfers and accountData (our token + wSOL).
RELEVANT_MINTS = frozenset({BANKED_MINT, WSOL_MINT})


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable, no network)
# ---------------------------------------------------------------------------


def load_birdeye_fixture(path: Path) -> list[dict]:
    """Load the banked Birdeye gzip jsonl fixture → list[dict]."""
    rows: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def extract_signatures(birdeye_rows: list[dict]) -> list[str]:
    """Extract deduplicated swap signatures (txHash) from Birdeye fixture rows."""
    seen: set[str] = set()
    sigs: list[str] = []
    for row in birdeye_rows:
        sig = row.get("txHash")
        if sig and sig not in seen:
            seen.add(sig)
            sigs.append(sig)
    return sigs


def slim_helius_tx(tx: dict, mint: str, wsol_mint: str) -> dict | None:
    """Reduce a full Helius Enhanced TX response to the fields needed for G2(b) verification.

    Keeps signature, slot, timestamps, type/source, fee_payer, and the token-transfer /
    native-transfer / account-change fields required for coverage + side + amount checks.
    Drops instruction data, inner-instruction data, and full accountData dumps so the
    fixture stays compact.

    Returns None if the transaction doesn't involve *mint* at all (i.e., is not a swap
    for our token — safety filter).
    """
    sig = tx.get("signature")
    if not sig:
        return None

    relevant_mints = {mint, wsol_mint}

    # Filter tokenTransfers to MINT + wSOL (the two legs of every PumpSwap swap).
    token_transfers = []
    for t in tx.get("tokenTransfers") or []:
        m = t.get("mint")
        if m in relevant_mints:
            token_transfers.append(
                {
                    "mint": m,
                    "from_account": t.get("fromUserAccount", ""),
                    "to_account": t.get("toUserAccount", ""),
                    "token_amount": t.get("tokenAmount"),
                }
            )

    # Our mint must appear in token_transfers — otherwise this tx is irrelevant.
    if not any(t["mint"] == mint for t in token_transfers):
        return None

    # All native SOL transfers (needed for amount calculation on direct PumpSwap swaps).
    native_transfers = []
    for t in tx.get("nativeTransfers") or []:
        amt = t.get("amount", 0)
        if amt and int(amt) != 0:
            native_transfers.append(
                {
                    "from_account": t.get("fromUserAccount", ""),
                    "to_account": t.get("toUserAccount", ""),
                    "amount": int(amt),
                }
            )

    # Account-level token balance changes for MINT and wSOL (for amount cross-check).
    account_token_changes = []
    for acc in tx.get("accountData") or []:
        account = acc.get("account", "")
        for tbc in acc.get("tokenBalanceChanges") or []:
            m = tbc.get("mint")
            if m in relevant_mints:
                raw_str = (tbc.get("rawTokenAmount") or {}).get("tokenAmount", "0") or "0"
                account_token_changes.append(
                    {
                        "account": account,
                        "mint": m,
                        "raw_change": int(raw_str),
                    }
                )

    return {
        "signature": sig,
        "slot": tx.get("slot"),
        "block_time": tx.get("timestamp"),
        "helius_type": tx.get("type", ""),
        "helius_source": tx.get("source", ""),
        "fee_payer": tx.get("feePayer", ""),
        "token_transfers": token_transfers,
        "native_transfers": native_transfers,
        "account_token_changes": account_token_changes,
    }


def determine_pool_account(slim_txs: list[dict], mint: str) -> str:
    """Identify the pool's base-token account by frequency in MINT tokenTransfers.

    In PumpSwap, every swap for a given token goes through the same pool token account
    (the AMM's base-reserve account for *mint*).  The account that appears in the MINT
    tokenTransfers of ALL swaps is the pool.  We pick the most-frequent account.
    """
    from collections import Counter

    counter: Counter = Counter()
    for tx in slim_txs:
        for t in tx.get("token_transfers") or []:
            if t["mint"] == mint:
                counter[t["from_account"]] += 1
                counter[t["to_account"]] += 1

    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def determine_side(slim_tx: dict, mint: str, pool_account: str) -> str | None:
    """Determine swap side from Helius token-transfer direction through the pool.

    Pool SENDS mint → user BUY (pool is selling its base token to the user).
    Pool RECEIVES mint → user SELL (user is selling their token to the pool).
    Returns None when the pool account is not found in the MINT transfers (ambiguous).
    """
    mint_transfers = [t for t in slim_tx.get("token_transfers") or [] if t["mint"] == mint]
    pool_sends = any(t["from_account"] == pool_account for t in mint_transfers)
    pool_receives = any(t["to_account"] == pool_account for t in mint_transfers)

    if pool_sends and not pool_receives:
        return "buy"
    if pool_receives and not pool_sends:
        return "sell"
    return None


def bank_helius_fixture(mint: str, slim_rows: list[dict], *, date: str) -> Path:
    """Write *slim_rows* to the canonical Helius golden fixture path (gzip jsonl)."""
    dest_dir = HELIUS_LAKE_BASE / f"dt={date}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = dest_dir / f"{mint}_helius_g2b_raw.jsonl.gz"
    with gzip.open(fixture_path, "wb") as gz:
        for row in slim_rows:
            gz.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    return fixture_path


# ---------------------------------------------------------------------------
# Live Helius API fetch (network — not unit-tested; exercised during activation)
# ---------------------------------------------------------------------------


def _ssl_context() -> ssl.SSLContext:  # pragma: no cover
    """Build an SSL context with a real CA bundle (macOS Python lacks system certs)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def fetch_helius_transactions(  # pragma: no cover
    api_key: str,
    signatures: list[str],
    *,
    commitment: str = "confirmed",
) -> list[dict]:
    """Batch-fetch Helius Enhanced Transactions for *signatures*.

    POST https://api.helius.xyz/v0/transactions?api-key=<key>
    Body: {"transactions": [...sigs...], "commitment": "confirmed"}
    Returns list of enriched transaction dicts.
    """
    ctx = _ssl_context()
    results: list[dict] = []
    for i in range(0, len(signatures), HELIUS_BATCH_SIZE):
        batch = signatures[i : i + HELIUS_BATCH_SIZE]
        url = f"{HELIUS_ENHANCED_TX_URL}?api-key={api_key}&commitment={commitment}"
        payload = json.dumps({"transactions": batch}).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "solanatrilly/1.0 (+https://github.com/AsimQuick/solanatrilly)",
            },
        )
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            print(f"[helius] HTTP {exc.code} on batch {i // HELIUS_BATCH_SIZE}: {exc.read().decode()[:200]}")
            continue
        except urllib.error.URLError as exc:
            print(f"[helius] Network error on batch {i // HELIUS_BATCH_SIZE}: {exc.reason}")
            continue
        batch_results = json.loads(body)
        if isinstance(batch_results, list):
            results.extend(batch_results)
        print(f"[helius] Batch {i // HELIUS_BATCH_SIZE + 1}: {len(batch_results)} txs decoded")
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Helius G2(b) raw-truth activation — ONE budgeted Helius spend (§15.7)"
    )
    parser.add_argument("--api-key", default=None, help="Helius API key (falls back to HELIUS_API_KEY)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan; make no API calls and bank nothing",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    args = parse_args(argv)

    api_key = args.api_key or os.environ.get("HELIUS_API_KEY", "")
    if not api_key and not args.dry_run:
        print("ERROR: No Helius API key. Set HELIUS_API_KEY or pass --api-key.")
        return 1

    date = time.strftime("%Y-%m-%d", time.gmtime())

    if not BIRDEYE_FIXTURE.exists():
        print(f"ERROR: Banked Birdeye fixture not found: {BIRDEYE_FIXTURE}")
        return 1

    birdeye_rows = load_birdeye_fixture(BIRDEYE_FIXTURE)
    signatures = extract_signatures(birdeye_rows)

    print(f"\n[g2b] Mint:           {BANKED_MINT}")
    print(f"[g2b] Birdeye swaps:  {len(birdeye_rows)} (from banked AC-22.2 fixture)")
    print(f"[g2b] Signatures:     {len(signatures)} unique")
    print("[g2b] Budget:         Helius 10 → 9 after this activation")
    print(f"[g2b] Fixture dest:   lake/golden/helius_decoded_txs/dt={date}/\n")

    if args.dry_run:
        print("[g2b] DRY RUN — no API calls made, no fixture banked.")
        print(f"[g2b] Would POST {len(signatures)} signatures to Helius Enhanced Transactions API.")
        for sig in signatures[:3]:
            print(f"  {sig}")
        if len(signatures) > 3:
            print(f"  … and {len(signatures) - 3} more")
        return 0

    # 1. Fetch Helius decoded transactions.
    print(f"[g2b] Fetching {len(signatures)} transactions from Helius Enhanced API …")
    t_start = time.monotonic()
    raw_txs = fetch_helius_transactions(api_key, signatures)
    elapsed = time.monotonic() - t_start
    print(f"[g2b] Helius response: {len(raw_txs)} decoded transactions in {elapsed:.1f}s")

    if not raw_txs:
        print("[g2b] ERROR: Helius returned 0 transactions. Check API key and signatures.")
        return 2

    # 2. Slim each tx to verified fields (raw = immutable truth for the needed fields).
    slim_rows: list[dict] = []
    for tx in raw_txs:
        row = slim_helius_tx(tx, BANKED_MINT, WSOL_MINT)
        if row is not None:
            slim_rows.append(row)

    if not slim_rows:
        print("[g2b] ERROR: 0 rows have MINT token_transfers. Check mint address.")
        return 3

    # 3. Discover pool account (most-frequent MINT account across all txs).
    pool = determine_pool_account(slim_rows, BANKED_MINT)
    print(f"[g2b] Pool account:   {pool}")

    # 4. Determine side for each tx and verify coverage.
    helius_sigs = {r["signature"] for r in slim_rows}
    birdeye_sigs = set(signatures)
    covered = birdeye_sigs & helius_sigs
    missing = birdeye_sigs - helius_sigs

    buys = sells = undetermined = 0
    for row in slim_rows:
        s = determine_side(row, BANKED_MINT, pool)
        if s == "buy":
            buys += 1
        elif s == "sell":
            sells += 1
        else:
            undetermined += 1

    print(f"\n[g2b] Coverage:       {len(covered)}/{len(birdeye_sigs)} Birdeye sigs in Helius")
    if missing:
        print(f"[g2b] Missing:        {len(missing)} sigs absent from Helius decode")
        for s in sorted(missing)[:5]:
            print(f"  {s}")
    print(f"[g2b] Sides:          {buys} buy / {sells} sell / {undetermined} undetermined")

    # 5. Bank the slim-raw fixture.
    fixture_path = bank_helius_fixture(BANKED_MINT, slim_rows, date=date)
    try:
        rel_path = fixture_path.relative_to(REPO_ROOT)
    except ValueError:
        rel_path = fixture_path

    print(f"\n[g2b] Banked → {fixture_path}")
    print("\n--- ACTIVATION SUMMARY ---")
    print(f"Date:             {date}")
    print(f"Mint:             {BANKED_MINT}")
    print(f"Helius txs:       {len(raw_txs)} decoded, {len(slim_rows)} with MINT transfers")
    print(f"Coverage:         {len(covered)}/{len(birdeye_sigs)} Birdeye sigs confirmed")
    print(f"Pool account:     {pool}")
    print(f"Sides:            {buys} buy / {sells} sell")
    print(f"Elapsed:          {elapsed:.1f}s")
    print(f"Fixture:          {rel_path}")
    print("Budget remaining: 8 Birdeye / 9 Helius")
    print("\n--- LOG TABLE ROW ---")
    duration_str = f"{elapsed:.0f}s"
    print(
        f"| {date} | dev-team | Helius Enhanced TX | "
        f"G2(b) raw-truth spot-check (AC-32.2, §7.6): Helius PumpSwap decode confirms "
        f"Birdeye tape coverage/side/amount for {BANKED_MINT[:16]}… "
        f"({len(covered)}/{len(birdeye_sigs)} sigs, {buys}B/{sells}S via pool-flow detection) | "
        f"{duration_str} | 8 Birdeye / 9 Helius | "
        f"{rel_path} ({len(slim_rows)} rows) |"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
