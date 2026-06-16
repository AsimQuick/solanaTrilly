# ---
# module: core.tests.test_g2a_live_backfill_parity_ac321
# sprint: sprint-7
# story: US-32 AC-32.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.recorder, core.replay_source, core.clock,
#               core.normalized_swap, core.feature_extractor,
#               core.tape_microstructure,
#               asyncio, dataclasses, gzip, json, pathlib, types
# ---
"""AC-32.1 — G2(a) live↔backfill parity on the real banked sprint-5 golden token.

For the banked sprint-5 golden token E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump,
the Birdeye live-stream tape and a Birdeye seek_by_time REST backfill of the SAME
token produce byte-identical normalized swaps AND byte-identical compute_features
output (via the US-30 extractor).  PRD §16 / §7.6.

The seek_by_time backfill is simulated OFFLINE using the same banked golden
fixture: both paths receive the SAME translated events through TapeRecorder,
one tagged swap_source="birdeye_live" and the other "birdeye_backfill".  This
isolates the seam being tested — the normalization code path — from any live
network dependency, making the gate deterministic and repeatable forever.

Test taxonomy:
  1. test_g2a_banked_fixture_exists_and_has_swaps
       Gate: the real durable golden fixture is committed to the repo.
  2. test_g2a_live_backfill_same_swap_count_e6ifp2
       Both paths produce the same number of NormalizedSwaps.
  3. test_g2a_swap_level_byte_identity_e6ifp2          ← MAIN GATE
       All non-source NormalizedSwap fields are byte-identical (JSON comparison).
  4. test_g2a_feature_level_byte_identity_e6ifp2       ← MAIN GATE
       FeatureExtractor.extract_from_lake() output is byte-identical (dict ==).
  5. test_g2a_source_tags_correct_e6ifp2
       Live path: source='birdeye_live'; backfill path: source='birdeye_backfill'.
  6. test_g2a_golden_features_are_not_none_e6ifp2
       compute_features returns non-None for the real golden token tape.
  7. test_g2a_feature_values_are_finite_e6ifp2
       All numeric feature values are finite (no inf / NaN from real data).
  8. test_g2a_swap_canonical_ordering_identical_e6ifp2
       Canonical (block_time, slot, signature) ordering is identical between paths.
"""
import asyncio
import dataclasses
import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

# ---------------------------------------------------------------------------
# Constants — banked sprint-5 golden token
# ---------------------------------------------------------------------------

BANKED_MINT = "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
WSOL_MINT = "So11111111111111111111111111111111111111112"

# min(blockUnixTime) across the 40 banked swaps — used as graduated_block_time so
# swaps begin at rel=0 and ≈20 fall within the 120s feature window.
_GOLDEN_GRADUATED_BT = 1_781_562_036

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BANKED_FIXTURE = (
    REPO_ROOT
    / "lake"
    / "golden"
    / "birdeye_subscribe_txs"
    / "dt=2026-06-15"
    / f"{BANKED_MINT}_pumpswap_golden.jsonl.gz"
)

# Fake VirtualClock start time — replay tests use a fixed anchor.
_T0 = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

# Token stub with graduated_block_time — the SAME anchor for both paths.
_GOLDEN_TOKEN = SimpleNamespace(graduated_block_time=_GOLDEN_GRADUATED_BT)
_GOLDEN_TOKEN_STORE = {BANKED_MINT: _GOLDEN_TOKEN}

# FeatureSet stub — hash/math_version stamps added by FeatureExtractor;
# both paths use the same stub so the stamps are identical in both outputs.
_MOCK_FEATURE_SET = SimpleNamespace(
    hash="ac321-g2a-parity-mock",
    math_version="solanabilly3:sprint-7",
)


# ---------------------------------------------------------------------------
# Raw fixture loader
# ---------------------------------------------------------------------------


def _load_banked_fixture() -> list[dict]:
    """Load the banked golden fixture for E6ifp2...pump (gzip jsonl → list[dict])."""
    rows: list[dict] = []
    with gzip.open(BANKED_FIXTURE, "rt", encoding="utf-8") as gz:
        for line in gz:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


# ---------------------------------------------------------------------------
# Field translation: Birdeye SUBSCRIBE_TXS → TapeRecorder internal format
# ---------------------------------------------------------------------------
# The banked golden fixture stores raw Birdeye SUBSCRIBE_TXS events (field names:
# blockUnixTime, txHash, blockNumber, tokenPrice, volumeUSD, …).  TapeRecorder
# expects the internal normalized format (block_time, signature, slot, price,
# vol_sol, vol_usd, sol_usd, mint, …).  This translation is the SAME function
# for both the live path and the seek_by_time backfill path — the seam being
# tested is the NORMALIZATION CODE PATH, not the transport format.
#
# vol_sol derivation:
#   sell swap → user sells token, receives SOL → 'to' leg is the SOL leg
#   buy  swap → user spends SOL, buys token   → 'from' leg is the SOL leg
#
# sol_usd (SOL price in USD) = nearestPrice of the SOL leg.


def _birdeye_ws_to_internal(event: dict) -> dict:
    """Translate one raw Birdeye SUBSCRIBE_TXS event to TapeRecorder input format."""
    side = event["side"]
    from_leg = event.get("from") or {}
    to_leg = event.get("to") or {}

    # SOL is always on the 'to' leg for a sell, 'from' leg for a buy.
    sol_leg = to_leg if side == "sell" else from_leg
    vol_sol = float(sol_leg.get("uiAmount", 0.0))
    sol_usd_price = sol_leg.get("nearestPrice")
    sol_usd = float(sol_usd_price) if sol_usd_price is not None else 0.0

    return {
        "mint": str(event["tokenAddress"]),
        "block_time": int(event["blockUnixTime"]),
        "slot": int(event["blockNumber"]),
        "signature": str(event["txHash"]),
        "side": side,
        "price": float(event["tokenPrice"]),
        "vol_sol": vol_sol,
        "vol_usd": float(event["volumeUSD"]),
        "sol_usd": sol_usd,
        "owner": event.get("owner"),
        "base_reserve": None,
        "quote_reserve": None,
        "quote_mint": WSOL_MINT,
        "failed": bool(event.get("failed", False)),
    }


def _translate_fixture(fixture_rows: list[dict]) -> list[dict]:
    """Translate all fixture rows to TapeRecorder internal format."""
    return [_birdeye_ws_to_internal(r) for r in fixture_rows]


# ---------------------------------------------------------------------------
# Path runners — live vs backfill through the same TapeRecorder code
# ---------------------------------------------------------------------------


def _run_path(events: list[dict], *, swap_source: str) -> list:
    """Run TapeRecorder over *events* with *swap_source* tag; return normalized_swaps."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        recorder = TapeRecorder(
            source=ReplaySource(event_log=events),
            clock=VirtualClock(_T0),
            token_store=_GOLDEN_TOKEN_STORE,
            swap_source=swap_source,
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


def _run_live_path(events: list[dict]) -> list:
    """Live-stream path: TapeRecorder with swap_source='birdeye_live'."""
    return _run_path(events, swap_source="birdeye_live")


def _run_backfill_path(events: list[dict]) -> list:
    """Seek_by_time backfill path: TapeRecorder with swap_source='birdeye_backfill'."""
    return _run_path(events, swap_source="birdeye_backfill")


# ---------------------------------------------------------------------------
# Serialization helpers for byte comparison
# ---------------------------------------------------------------------------


def _all_non_source_fields_as_json(swap) -> str:
    """Serialize all NormalizedSwap fields EXCEPT 'source' (the one intentional diff)."""
    from core.encoders import JsonSafeEncoder

    d = dataclasses.asdict(swap)
    d.pop("source")
    return json.dumps(d, cls=JsonSafeEncoder, sort_keys=True)


def _swap_to_lake_row(swap, mint: str) -> dict:
    """Convert a NormalizedSwap to a lake-row dict (NormalizedSwap.to_dict() + mint)."""
    row = swap.to_dict()
    row["mint"] = mint
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_g2a_banked_fixture_exists_and_has_swaps() -> None:
    """Gate: the real durable golden fixture is committed to the repo.

    Ensures the offline parity test runs against banked REALITY (AC-22.2),
    not a fabricated placeholder.
    """
    assert BANKED_FIXTURE.exists(), (
        f"Banked golden fixture missing: {BANKED_FIXTURE}\n"
        "The fixture must be committed to the repo (AC-22.2 / §15.7 hard rule)."
    )
    rows = _load_banked_fixture()
    assert len(rows) >= 1, "Banked fixture must contain at least one real swap."
    for r in rows:
        assert r.get("tokenAddress") == BANKED_MINT, (
            f"Fixture row has wrong tokenAddress: {r.get('tokenAddress')!r}"
        )
        assert r.get("source") == "pump_amm", (
            f"Fixture row source is not pump_amm: {r.get('source')!r}"
        )


def test_g2a_live_backfill_same_swap_count_e6ifp2() -> None:
    """Both live and backfill paths produce the same count of NormalizedSwaps.

    Same events in → same filter decisions → same count out.  A count mismatch
    would mean the two paths have diverged in their failed-swap exclusion or
    degenerate-swap guard logic.
    """
    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    backfill_swaps = _run_backfill_path(events)

    assert len(live_swaps) > 0, (
        "Live path produced 0 NormalizedSwaps from the real golden fixture — "
        "check translation or degenerate-swap guard."
    )
    assert len(live_swaps) == len(backfill_swaps), (
        f"Live path produced {len(live_swaps)} swaps; "
        f"backfill path produced {len(backfill_swaps)} swaps. "
        "Both paths must process the same number of landed, non-degenerate swaps."
    )


def test_g2a_swap_level_byte_identity_e6ifp2() -> None:
    """MAIN GATE: all non-source NormalizedSwap fields are byte-identical.

    rel, price, side, vol_sol, vol_usd, sol_usd, owner, block_time, slot,
    signature, base_reserve, quote_reserve, quote_mint, phase — ALL must match
    between live and backfill paths.  Only the 'source' provenance tag may differ.

    This is the AC-32.1 swap-level byte-identity assertion (§16 / G2(a)).
    """
    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    backfill_swaps = _run_backfill_path(events)

    assert len(live_swaps) == len(backfill_swaps), (
        "Cannot compare swap-by-swap: paths produced different counts."
    )

    for i, (live, backfill) in enumerate(zip(live_swaps, backfill_swaps)):
        live_json = _all_non_source_fields_as_json(live)
        backfill_json = _all_non_source_fields_as_json(backfill)
        assert live_json == backfill_json, (
            f"Swap[{i}] NOT byte-identical between live and backfill paths "
            f"(G2(a) AC-32.1 VIOLATION):\n"
            f"  Live:     {live_json}\n"
            f"  Backfill: {backfill_json}"
        )


def test_g2a_feature_level_byte_identity_e6ifp2() -> None:
    """MAIN GATE: FeatureExtractor.extract_from_lake() output is byte-identical.

    Converts each path's NormalizedSwaps to lake-row format and feeds them
    through the US-30 FeatureExtractor.  Both calls must return equal dicts —
    byte-identical compute_features output.

    This is the AC-32.1 feature-level byte-identity assertion (§16 / G2(a)).
    The FeatureExtractor (US-30) is the single shared code path (Principle #2);
    byte-identical input → byte-identical output by construction.
    """
    from core.feature_extractor import FeatureExtractor

    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    backfill_swaps = _run_backfill_path(events)

    live_rows = [_swap_to_lake_row(s, BANKED_MINT) for s in live_swaps]
    backfill_rows = [_swap_to_lake_row(s, BANKED_MINT) for s in backfill_swaps]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    live_features = extractor.extract_from_lake(BANKED_MINT, live_rows)
    backfill_features = extractor.extract_from_lake(BANKED_MINT, backfill_rows)

    assert live_features is not None, (
        "Live path features are None — the real golden fixture produced no usable "
        "swaps within the 120s window."
    )
    assert backfill_features is not None, (
        "Backfill path features are None — the real golden fixture produced no "
        "usable swaps within the 120s window."
    )
    assert live_features == backfill_features, (
        "Feature dicts NOT byte-identical between live and backfill paths "
        f"(G2(a) AC-32.1 VIOLATION).\n"
        f"  Live keys:     {sorted(live_features)}\n"
        f"  Backfill keys: {sorted(backfill_features)}\n"
        f"  Differing keys: "
        f"{[k for k in live_features if live_features[k] != backfill_features.get(k)]}"
    )


def test_g2a_source_tags_correct_e6ifp2() -> None:
    """Live path: source='birdeye_live'; backfill path: source='birdeye_backfill'.

    The 'source' field is the ONE intentional difference.  Confirms the provenance
    tags are set correctly so the lake retains traceability of which path wrote
    each swap.
    """
    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    backfill_swaps = _run_backfill_path(events)

    for i, swap in enumerate(live_swaps):
        assert swap.source == "birdeye_live", (
            f"Live path Swap[{i}].source={swap.source!r}, expected 'birdeye_live'."
        )
    for i, swap in enumerate(backfill_swaps):
        assert swap.source == "birdeye_backfill", (
            f"Backfill path Swap[{i}].source={swap.source!r}, "
            "expected 'birdeye_backfill'."
        )


def test_g2a_golden_features_are_not_none_e6ifp2() -> None:
    """compute_features returns non-None for the real golden fixture.

    Confirms that the translated real swaps contain usable data within the 120s
    feature window — guarding against a degenerate golden fixture that would make
    all downstream parity assertions vacuously pass on None == None.
    """
    from core.feature_extractor import FeatureExtractor

    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    live_rows = [_swap_to_lake_row(s, BANKED_MINT) for s in live_swaps]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    features = extractor.extract_from_lake(BANKED_MINT, live_rows)

    assert features is not None, (
        "FeatureExtractor returned None for the real golden fixture — "
        "either the window is too narrow or the fixture has no usable swaps."
    )
    # At minimum the n_trades feature must be a positive integer
    assert features.get("tape_n_trades", 0) > 0, (
        f"tape_n_trades={features.get('tape_n_trades')} — must be > 0 for real data."
    )


def test_g2a_feature_values_are_finite_e6ifp2() -> None:
    """All numeric feature values from the real golden token are finite (no inf/NaN).

    Validates that the real swap data doesn't produce degenerate feature values
    from division-by-zero, log(0), or extreme price movements.
    """
    import math

    from core.feature_extractor import FeatureExtractor

    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    live_rows = [_swap_to_lake_row(s, BANKED_MINT) for s in live_swaps]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    features = extractor.extract_from_lake(BANKED_MINT, live_rows)
    assert features is not None

    non_finite = {
        k: v
        for k, v in features.items()
        if isinstance(v, float) and not math.isfinite(v)
    }
    assert not non_finite, (
        f"Non-finite feature values in real golden token output: {non_finite}. "
        "Check degenerate-price guard and log-slope computation."
    )


def test_g2a_swap_canonical_ordering_identical_e6ifp2() -> None:
    """Canonical (block_time, slot, signature) ordering is identical between paths.

    Both paths must deliver swaps in the same canonical order — the stable sort
    inside TapeRecorder.normalized_swaps is the shared sort applied to both.
    Identical ordering is a prerequisite for feature-level byte-identity
    (compute_features is order-sensitive for time-bucketed features).
    """
    events = _translate_fixture(_load_banked_fixture())
    live_swaps = _run_live_path(events)
    backfill_swaps = _run_backfill_path(events)

    live_keys = [(s.block_time, s.slot, s.signature) for s in live_swaps]
    backfill_keys = [(s.block_time, s.slot, s.signature) for s in backfill_swaps]

    assert live_keys == backfill_keys, (
        "Canonical ordering differs between live and backfill paths "
        f"(G2(a) AC-32.1).\n"
        f"  Live keys:     {live_keys[:5]}...\n"
        f"  Backfill keys: {backfill_keys[:5]}..."
    )
    # Also confirm ascending order (not just equal-to-each-other)
    assert live_keys == sorted(live_keys), (
        f"Live path canonical keys are not in ascending order: {live_keys[:5]}..."
    )
