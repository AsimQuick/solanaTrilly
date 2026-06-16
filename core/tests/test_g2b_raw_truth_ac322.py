# ---
# module: core.tests.test_g2b_raw_truth_ac322
# sprint: sprint-7
# story: US-32 AC-32.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: gzip, json, math, pathlib, collections
# ---
"""AC-32.2 — G2(b) raw-truth spot-check: Helius PumpSwap decode confirms Birdeye tape
coverage, side, and amount on the banked golden token.

This test runs OFFLINE forever against two durable banked fixtures:
  1. Birdeye SUBSCRIBE_TXS fixture (AC-22.2, 40 real PumpSwap swaps)
  2. Helius Enhanced TX fixture (AC-32.2, banked by tools/helius_g2b_activate.py)

The P5 gate NEVER depends on a live Helius read.  The Helius activation is
the project's ONE budgeted G2 firehose spend (CLAUDE.md / ops/firehose_activation_log.md).

Side detection (pool-flow approach):
  The PumpSwap pool's base-token account (`DZxWcyPpTyr2NTfmEN2x…`) appears in the
  MINT token_transfers of every swap.  Side = BUY if the pool SENDS the MINT token;
  side = SELL if the pool RECEIVES the MINT token.  This correctly classifies both
  direct PumpSwap transactions and aggregator-routed transactions.

Amount cross-check:
  vol_sol from Helius is derived from the largest wSOL tokenTransfer in the expected
  direction relative to the pool.  Tolerance: 5% to accommodate protocol fees and
  routing-leg differences between what Birdeye reports (the user's net amount) and
  what Helius sees in raw token transfers (which may include fee disbursements).

Test taxonomy (8 tests):
  1. test_g2b_helius_fixture_exists_and_has_swaps
       Gate: the banked Helius fixture is committed to the repo.
  2. test_g2b_birdeye_fixture_exists_and_has_swaps
       Gate: the banked Birdeye fixture is committed to the repo.
  3. test_g2b_coverage_100pct                           ← MAIN GATE
       Every Birdeye signature is present in the Helius decode.
  4. test_g2b_side_parity_100pct                        ← MAIN GATE
       Helius pool-flow side matches Birdeye side for every swap.
  5. test_g2b_amount_parity_within_tolerance
       Helius vol_sol is within 5% of Birdeye vol_sol for ≥90% of swaps.
  6. test_g2b_pool_account_consistent
       The pool account discovered across all Helius txs appears in ALL swaps.
  7. test_g2b_no_undetermined_sides
       Pool-flow detection resolves side for all 40 swaps (no undetermined).
  8. test_g2b_buy_sell_counts_match_birdeye
       Helius buy/sell counts exactly match the Birdeye tape.
"""
import gzip
import json
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — banked fixtures
# ---------------------------------------------------------------------------

BANKED_MINT = "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
WSOL_MINT = "So11111111111111111111111111111111111111112"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

BIRDEYE_FIXTURE = (
    REPO_ROOT
    / "lake"
    / "golden"
    / "birdeye_subscribe_txs"
    / "dt=2026-06-15"
    / f"{BANKED_MINT}_pumpswap_golden.jsonl.gz"
)

HELIUS_FIXTURE = (
    REPO_ROOT
    / "lake"
    / "golden"
    / "helius_decoded_txs"
    / "dt=2026-06-16"
    / f"{BANKED_MINT}_helius_g2b_raw.jsonl.gz"
)

# Tolerance for vol_sol amount comparison (5% to accommodate routing fees).
VOL_SOL_TOLERANCE = 0.05

# Minimum fraction of swaps that must pass the amount check.
AMOUNT_PARITY_MIN_FRACTION = 0.90


# ---------------------------------------------------------------------------
# Fixture loaders
# ---------------------------------------------------------------------------


def _load_fixture(path: Path) -> list[dict]:
    """Load a gzip jsonl fixture into a list of dicts."""
    rows: list[dict] = []
    with gzip.open(path, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Pool-flow side detection (pure, no network)
# ---------------------------------------------------------------------------


def _discover_pool_account(helius_rows: list[dict], mint: str) -> str:
    """Find the pool's base-token account from the Helius fixture.

    The PumpSwap pool account appears in the MINT token_transfers of every swap.
    The most-frequent MINT account across all rows is the pool.
    """
    counter: Counter = Counter()
    for row in helius_rows:
        for t in row.get("token_transfers") or []:
            if t.get("mint") == mint:
                counter[t.get("from_account", "")] += 1
                counter[t.get("to_account", "")] += 1
    if not counter:
        return ""
    return counter.most_common(1)[0][0]


def _determine_side(helius_row: dict, mint: str, pool_account: str) -> str | None:
    """Determine swap side from Helius token-transfer direction through the pool.

    Pool SENDS mint → BUY (pool sells base token to user).
    Pool RECEIVES mint → SELL (user sells base token to pool).
    Returns None when the pool is not found in the MINT transfers (unexpected).
    """
    mint_transfers = [t for t in (helius_row.get("token_transfers") or []) if t.get("mint") == mint]
    pool_sends = any(t.get("from_account") == pool_account for t in mint_transfers)
    pool_receives = any(t.get("to_account") == pool_account for t in mint_transfers)

    if pool_sends and not pool_receives:
        return "buy"
    if pool_receives and not pool_sends:
        return "sell"
    return None


# ---------------------------------------------------------------------------
# Volume extraction from Helius token_transfers
# ---------------------------------------------------------------------------


def _helius_vol_sol(helius_row: dict, side: str, pool_account: str) -> float | None:
    """Extract vol_sol (SOL volume) from Helius wSOL token_transfers.

    For a SELL: the pool SENDS wSOL to the user → take the largest wSOL outflow
    from the pool account.
    For a BUY: the pool RECEIVES wSOL from the user → take the largest wSOL inflow
    to the pool account.

    Falls back to the largest native SOL transfer in the transaction if wSOL
    token_transfers don't show a direct pool connection (e.g. unwrapped SOL).
    Returns None when no SOL flow can be identified.
    """
    wsol_transfers = [t for t in (helius_row.get("token_transfers") or []) if t.get("mint") == WSOL_MINT]

    if side == "sell":
        # Pool pays wSOL out: largest wSOL transfer FROM the pool.
        amounts = [
            float(t.get("token_amount") or 0)
            for t in wsol_transfers
            if t.get("from_account") == pool_account and float(t.get("token_amount") or 0) > 0
        ]
        if amounts:
            return max(amounts)
    elif side == "buy":
        # Pool receives wSOL in: the direct inflow to the pool may be in native SOL
        # (Helius tracks it as a nativeTransfer).  Use native SOL transfers as proxy.
        # First try wSOL TO the pool.
        amounts = [
            float(t.get("token_amount") or 0)
            for t in wsol_transfers
            if t.get("to_account") == pool_account and float(t.get("token_amount") or 0) > 0
        ]
        if amounts:
            return max(amounts)

    # Fallback: largest native SOL transfer.
    native_transfers = helius_row.get("native_transfers") or []
    if native_transfers:
        lampts = [int(t.get("amount", 0)) for t in native_transfers if int(t.get("amount", 0)) > 0]
        if lampts:
            return max(lampts) / 1e9

    return None


# ---------------------------------------------------------------------------
# Birdeye vol_sol extraction
# ---------------------------------------------------------------------------


def _birdeye_vol_sol(birdeye_row: dict) -> float | None:
    """Extract vol_sol from the Birdeye fixture row.

    For a SELL: user receives SOL → `to` leg is SOL; vol_sol = to.uiAmount.
    For a BUY: user spends SOL → `from` leg is SOL; vol_sol = from.uiAmount.
    """
    side = birdeye_row.get("side", "")
    leg_key = "to" if side == "sell" else "from"
    leg = birdeye_row.get(leg_key) or {}
    amt = leg.get("uiAmount")
    return float(amt) if amt is not None else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_g2b_helius_fixture_exists_and_has_swaps() -> None:
    """Gate: the banked Helius raw-truth fixture is committed to the repo.

    Ensures the G2(b) offline parity test runs against banked reality, not a
    fabricated placeholder.  The fixture was banked by the project's ONE budgeted
    Helius activation (tools/helius_g2b_activate.py, ops/firehose_activation_log.md).
    """
    assert HELIUS_FIXTURE.exists(), (
        f"Banked Helius G2(b) fixture missing: {HELIUS_FIXTURE}\n"
        "Run: python tools/helius_g2b_activate.py (the ONE budgeted Helius activation)."
    )
    rows = _load_fixture(HELIUS_FIXTURE)
    assert len(rows) >= 1, "Helius fixture must contain at least one decoded transaction."
    for r in rows:
        assert "signature" in r, f"Fixture row missing 'signature' key: {list(r)[:5]}"
        assert "token_transfers" in r, f"Fixture row missing 'token_transfers': {r.get('signature','?')[:20]}"
        mint_tts = [t for t in r["token_transfers"] if t.get("mint") == BANKED_MINT]
        assert len(mint_tts) >= 1, (
            f"Fixture row {r['signature'][:20]}… has no MINT token_transfers for {BANKED_MINT[:16]}…"
        )


def test_g2b_birdeye_fixture_exists_and_has_swaps() -> None:
    """Gate: the banked Birdeye golden fixture (AC-22.2) is committed to the repo.

    The Birdeye fixture is the source of truth for the 40 golden signatures against
    which the Helius decode is cross-checked.
    """
    assert BIRDEYE_FIXTURE.exists(), (
        f"Banked Birdeye golden fixture missing: {BIRDEYE_FIXTURE}\n"
        "The fixture must be committed (AC-22.2)."
    )
    rows = _load_fixture(BIRDEYE_FIXTURE)
    assert len(rows) >= 1, "Birdeye fixture must contain at least one real swap."
    for r in rows:
        assert r.get("tokenAddress") == BANKED_MINT, (
            f"Birdeye row has unexpected tokenAddress: {r.get('tokenAddress','?')!r}"
        )


def test_g2b_coverage_100pct() -> None:
    """MAIN GATE: every Birdeye signature is present in the Helius decode.

    Coverage = 100% means Helius independently confirms all 40 real PumpSwap swap
    signatures exist on-chain and were decoded successfully.  Any missing signature
    would indicate Birdeye reported a transaction that Helius cannot confirm — a
    coverage gap and a G2(b) VIOLATION.
    """
    birdeye_rows = _load_fixture(BIRDEYE_FIXTURE)
    helius_rows = _load_fixture(HELIUS_FIXTURE)

    birdeye_sigs = {r["txHash"] for r in birdeye_rows}
    helius_sigs = {r["signature"] for r in helius_rows}

    missing = birdeye_sigs - helius_sigs
    assert not missing, (
        f"G2(b) COVERAGE VIOLATION: {len(missing)}/{len(birdeye_sigs)} Birdeye signatures "
        f"are absent from the Helius decode.\n"
        f"Missing: {sorted(missing)[:3]}…\n"
        "Re-run tools/helius_g2b_activate.py to refresh the fixture."
    )
    assert len(birdeye_sigs) == len(helius_sigs), (
        f"Helius fixture has {len(helius_sigs)} rows but Birdeye has {len(birdeye_sigs)} sigs — "
        "extra or missing Helius rows detected."
    )


def test_g2b_side_parity_100pct() -> None:
    """MAIN GATE: Helius pool-flow side matches Birdeye side for every swap.

    Side is determined from the direction of the MINT token through the PumpSwap
    pool's base-token account (pool-flow approach — see module docstring).  A side
    mismatch means Helius and Birdeye disagree on whether the user was buying or
    selling — a fundamental raw-truth discrepancy and a G2(b) VIOLATION.
    """
    birdeye_rows = _load_fixture(BIRDEYE_FIXTURE)
    helius_rows = _load_fixture(HELIUS_FIXTURE)

    birdeye_by_sig = {r["txHash"]: r for r in birdeye_rows}
    pool = _discover_pool_account(helius_rows, BANKED_MINT)
    assert pool, "Pool account not found in Helius fixture — cannot determine side."

    mismatches: list[str] = []
    for h_row in helius_rows:
        sig = h_row["signature"]
        b_row = birdeye_by_sig.get(sig)
        if b_row is None:
            continue
        b_side = b_row.get("side")
        h_side = _determine_side(h_row, BANKED_MINT, pool)

        if h_side != b_side:
            mismatches.append(
                f"  Swap {sig[:20]}… Birdeye={b_side!r} Helius={h_side!r}"
            )

    assert not mismatches, (
        f"G2(b) SIDE VIOLATION: {len(mismatches)} swaps have mismatched sides:\n"
        + "\n".join(mismatches[:5])
        + f"\n(pool={pool[:20]}…)"
    )


def test_g2b_amount_parity_within_tolerance() -> None:
    """Helius vol_sol is within 5% of Birdeye vol_sol for ≥90% of swaps.

    Exact vol_sol equality is not guaranteed: Birdeye reports the user's NET SOL
    amount while Helius tracks the full raw wSOL flow (which includes protocol fees
    disbursed to LP/referral accounts).  A 5% tolerance accommodates these routing
    differences.  The requirement of ≥90% matching provides a hard statistical floor
    that would catch systematic mis-accounting while allowing individual edge-case
    variations.
    """
    birdeye_rows = _load_fixture(BIRDEYE_FIXTURE)
    helius_rows = _load_fixture(HELIUS_FIXTURE)

    birdeye_by_sig = {r["txHash"]: r for r in birdeye_rows}
    pool = _discover_pool_account(helius_rows, BANKED_MINT)

    within_tolerance = 0
    total_compared = 0
    details: list[str] = []

    for h_row in helius_rows:
        sig = h_row["signature"]
        b_row = birdeye_by_sig.get(sig)
        if b_row is None:
            continue

        b_vol = _birdeye_vol_sol(b_row)
        h_side = _determine_side(h_row, BANKED_MINT, pool)
        h_vol = _helius_vol_sol(h_row, h_side, pool) if h_side else None

        if b_vol is None or h_vol is None or b_vol == 0:
            continue

        total_compared += 1
        relative_diff = abs(h_vol - b_vol) / b_vol
        if relative_diff <= VOL_SOL_TOLERANCE:
            within_tolerance += 1
        else:
            details.append(
                f"  {sig[:20]}… B_vol={b_vol:.6f} H_vol={h_vol:.6f} diff={relative_diff:.1%}"
            )

    assert total_compared > 0, (
        "No swaps were compared for amount parity — check vol_sol extraction helpers."
    )
    fraction = within_tolerance / total_compared
    assert fraction >= AMOUNT_PARITY_MIN_FRACTION, (
        f"G2(b) AMOUNT VIOLATION: {within_tolerance}/{total_compared} swaps within "
        f"{VOL_SOL_TOLERANCE:.0%} vol_sol tolerance "
        f"({fraction:.1%} < required {AMOUNT_PARITY_MIN_FRACTION:.0%}).\n"
        "Outliers:\n" + "\n".join(details[:5])
    )


def test_g2b_pool_account_consistent() -> None:
    """The pool account appears in ALL 40 MINT token_transfers in the Helius fixture.

    The PumpSwap pool's base-token account is the invariant anchor of the pool-flow
    side detection.  If it is absent from any swap's MINT transfers, the pool-flow
    approach would silently return an undetermined side — breaking the parity gate.
    """
    helius_rows = _load_fixture(HELIUS_FIXTURE)
    pool = _discover_pool_account(helius_rows, BANKED_MINT)
    assert pool, "Could not discover pool account from Helius fixture."

    missing_pool: list[str] = []
    for row in helius_rows:
        mint_tts = [t for t in (row.get("token_transfers") or []) if t.get("mint") == BANKED_MINT]
        in_any = any(t.get("from_account") == pool or t.get("to_account") == pool for t in mint_tts)
        if not in_any:
            missing_pool.append(row["signature"][:20] + "…")

    assert not missing_pool, (
        f"Pool account {pool[:20]}… is absent from MINT token_transfers in "
        f"{len(missing_pool)} swap(s):\n" + "\n".join(missing_pool[:5])
    )


def test_g2b_no_undetermined_sides() -> None:
    """Pool-flow detection resolves side (buy or sell) for all 40 Helius swaps.

    No undetermined sides means the pool-flow approach is complete for this dataset —
    every swap's MINT token_transfers include the pool account on exactly one side.
    An undetermined side would leave that swap uncovered by the parity gate.
    """
    helius_rows = _load_fixture(HELIUS_FIXTURE)
    pool = _discover_pool_account(helius_rows, BANKED_MINT)

    undetermined: list[str] = []
    for row in helius_rows:
        side = _determine_side(row, BANKED_MINT, pool)
        if side is None:
            undetermined.append(row["signature"][:20] + "…")

    assert not undetermined, (
        f"{len(undetermined)} swap(s) have undetermined side (pool not on exactly one side):\n"
        + "\n".join(undetermined[:5])
    )


def test_g2b_buy_sell_counts_match_birdeye() -> None:
    """Helius buy/sell counts exactly match the Birdeye tape.

    Birdeye reported 23 buy / 17 sell swaps.  Helius pool-flow detection must
    produce the same aggregate counts — any discrepancy indicates systematic
    side mis-classification in either Birdeye or our Helius detection logic.
    """
    birdeye_rows = _load_fixture(BIRDEYE_FIXTURE)
    helius_rows = _load_fixture(HELIUS_FIXTURE)
    pool = _discover_pool_account(helius_rows, BANKED_MINT)

    birdeye_buys = sum(1 for r in birdeye_rows if r.get("side") == "buy")
    birdeye_sells = sum(1 for r in birdeye_rows if r.get("side") == "sell")

    helius_buys = sum(
        1 for r in helius_rows if _determine_side(r, BANKED_MINT, pool) == "buy"
    )
    helius_sells = sum(
        1 for r in helius_rows if _determine_side(r, BANKED_MINT, pool) == "sell"
    )

    assert helius_buys == birdeye_buys, (
        f"Buy count mismatch: Helius={helius_buys}, Birdeye={birdeye_buys}."
    )
    assert helius_sells == birdeye_sells, (
        f"Sell count mismatch: Helius={helius_sells}, Birdeye={birdeye_sells}."
    )
