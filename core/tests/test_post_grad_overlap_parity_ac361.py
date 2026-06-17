# ---
# module: core.tests.test_post_grad_overlap_parity_ac361
# sprint: sprint-8
# story: US-36 AC-36.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.helius_birth_tape_source, core.tape.mapped_source,
#               core.tape.recorder, core.replay_source, core.clock,
#               core.normalized_swap, core.feature_extractor,
#               core.tape_microstructure, core.encoders,
#               tools.helius_birth_tape_activate,
#               core.tests.test_birth_tape_live_fixture_ac343,
#               asyncio, dataclasses, json, pathlib, types
# ---
"""AC-36.1 — Post-graduation OVERLAP byte-parity: the anti-drift contract.

For a mint whose tape exists in BOTH sources after graduation, the helius_live
birth-tape and the Birdeye tape produce byte-identical normalized swaps AND
byte-identical compute_features output (via the US-30 extractor) in the overlap
window.  Offline + deterministic over the banked US-34 birth-tape golden fixture.

This test FOLDS the helius_live source into the existing US-32 gate — it does NOT
create a parallel gate.  The two MAIN GATE functions are pinned by name in
test_parity_gate_ac323.py (the combined T0/G1/G2 wire) so a deletion or rename
breaks pytest collection immediately (AC-32.3 ImportError-trap pattern).

Overlap window definition
--------------------------
The overlap window = the post-graduation portion of the tape where BOTH sources
can observe the same on-chain events:
  - helius_live: program-wide firehose sees ALL swaps (pre + post graduation)
  - birdeye_live: per-mint subscription can only subscribe AFTER graduation

This test simulates the Birdeye path by replaying the SAME decoded post-grad
events through TapeRecorder with swap_source='birdeye_live'.  This correctly
isolates the normalization code path (the seam being tested) from any live
network dependency — exactly as AC-32.1 tests Birdeye live↔backfill parity by
replaying the same fixture through both paths.

The golden fixture (lake/golden/helius_birth_tape/dt=2026-06-17/
helius_birth_tape_pregrad_golden.jsonl.gz) carries 6 rows:
  3 pre-grad trade events + 1 migrate (→ None from decoder) + 2 post-grad trades.
The 2 post-grad swaps (rel=100s and rel=200s) form the overlap window under test.

Test taxonomy
--------------
  1. test_overlap_fixture_exists_and_has_post_grad_swaps
       Gate: the banked golden fixture exists and has post-graduation trade rows.
  2. test_overlap_same_count_in_overlap_window
       Both paths produce the same count of normalized swaps in the overlap window.
  3. test_overlap_swap_level_byte_identity               ← MAIN GATE
       All non-source NormalizedSwap fields are byte-identical in the overlap window.
  4. test_overlap_feature_level_byte_identity            ← MAIN GATE
       FeatureExtractor.extract_from_lake() output is byte-identical (dict ==).
  5. test_overlap_source_tags_correct
       helius path: source='helius_live'; birdeye path: source='birdeye_live'.
  6. test_overlap_post_grad_swaps_have_positive_rel
       All swaps in the overlap window have rel >= 0 (post-graduation anchoring).
  7. test_overlap_features_non_none
       compute_features returns non-None — the test is not vacuously passing on None==None.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.tape.helius_birth_tape_source import decode_helius_notification
from tools.helius_birth_tape_activate import load_birth_tape_fixture

# Import public constants + idempotent fixture-creator from the AC-34.3 module.
# Importing this module also runs its module-level _ensure_fixture_exists(), which
# creates the committed golden file on fresh clones — idempotent and side-effect-free.
from core.tests.test_birth_tape_live_fixture_ac343 import (
    GOLDEN_MINT,
    GRADUATED_BT,
    _ensure_fixture_exists,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXTURE_DATE: str = "2026-06-17"

# Ensure the committed golden fixture is present (or regenerate it deterministically).
_FIXTURE_PATH: Path = _ensure_fixture_exists()

# Token stub: the graduation anchor matches the committed fixture exactly.
_OVERLAP_TOKEN = SimpleNamespace(graduated_block_time=GRADUATED_BT)
_OVERLAP_TOKEN_STORE: dict = {GOLDEN_MINT: _OVERLAP_TOKEN}

# Fixed clock anchor — replay tests use a deterministic datetime.
_T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)

# FeatureSet stub — identical hash/math_version stamps in both paths so the
# _feature_set_hash / _math_version metadata is byte-identical.
_MOCK_FEATURE_SET = SimpleNamespace(
    hash="ac361-overlap-parity-mock",
    math_version="solanabilly3:sprint-8",
)

# Feature extraction window chosen to include BOTH post-grad swaps (rel=100s and
# rel=200s) so the feature comparison is over 2 real swaps, not just 1.
_FEATURE_WINDOW_S: int = 250


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _load_fixture_rows() -> list[dict]:
    """Load the AC-34.3 golden birth-tape fixture rows from disk."""
    return load_birth_tape_fixture(_FIXTURE_PATH)


def _get_all_decoded_events(raw_rows: list[dict]) -> list[dict]:
    """Decode all raw notification rows; drop None (migrate / failed / malformed)."""
    return [d for d in (decode_helius_notification(r) for r in raw_rows) if d is not None]


def _get_post_grad_decoded_events(all_events: list[dict]) -> list[dict]:
    """Filter decoded trade events to the overlap window (block_time >= GRADUATED_BT)."""
    return [e for e in all_events if int(e["block_time"]) >= GRADUATED_BT]


# ---------------------------------------------------------------------------
# Path runners
# ---------------------------------------------------------------------------


def _run_helius_path(raw_rows: list[dict]) -> list:
    """Run raw Helius notifications → MappedSwapSource → TapeRecorder (helius_live).

    Returns all normalized_swaps (pre + post graduation).  Caller filters to
    the overlap window with _filter_to_overlap().
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.mapped_source import MappedSwapSource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        source = MappedSwapSource(
            ReplaySource(event_log=raw_rows),
            decode_helius_notification,
        )
        recorder = TapeRecorder(
            source=source,
            clock=VirtualClock(_T0),
            token_store=_OVERLAP_TOKEN_STORE,
            swap_source="helius_live",
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


def _run_birdeye_simulation_path(post_grad_events: list[dict]) -> list:
    """Replay post-grad decoded events tagged as birdeye_live through TapeRecorder.

    Simulates what Birdeye would produce after subscribing to the mint at graduation:
    the same on-chain events, decoded to internal format, tagged 'birdeye_live'.
    The seam being tested is the NORMALIZATION CODE PATH — not the transport.
    """
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        recorder = TapeRecorder(
            source=ReplaySource(event_log=post_grad_events),
            clock=VirtualClock(_T0),
            token_store=_OVERLAP_TOKEN_STORE,
            swap_source="birdeye_live",
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


# ---------------------------------------------------------------------------
# Filter and serialization helpers
# ---------------------------------------------------------------------------


def _filter_to_overlap(swaps: list) -> list:
    """Filter normalized_swaps to the overlap window (rel >= 0, post-graduation)."""
    return [s for s in swaps if s.rel >= 0]


def _all_non_source_fields_as_json(swap) -> str:
    """Serialize all NormalizedSwap fields EXCEPT 'source' (the one intentional diff)."""
    from core.encoders import JsonSafeEncoder

    d = dataclasses.asdict(swap)
    d.pop("source")
    return json.dumps(d, cls=JsonSafeEncoder, sort_keys=True)


def _swap_to_lake_row(swap) -> dict:
    """Convert a NormalizedSwap to a lake-row dict via to_dict() (includes 'mint' per AC-34.2)."""
    return swap.to_dict()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_overlap_fixture_exists_and_has_post_grad_swaps() -> None:
    """Gate: the banked golden fixture exists and contains post-graduation trade rows.

    Ensures the overlap parity test runs against banked reality, not a fabricated
    placeholder.  The fixture must have at least one post-graduation trade event
    (block_time >= GRADUATED_BT) so the overlap window is non-empty.
    """
    assert _FIXTURE_PATH.exists(), (
        f"Golden birth-tape fixture missing: {_FIXTURE_PATH}\n"
        "The US-34 AC-34.3 committed fixture must be present in the repo."
    )
    raw_rows = _load_fixture_rows()
    assert len(raw_rows) >= 1, "Fixture file is empty — may be corrupt."

    all_events = _get_all_decoded_events(raw_rows)
    post_grad = _get_post_grad_decoded_events(all_events)

    assert len(post_grad) >= 1, (
        f"No post-graduation decoded events found (block_time >= {GRADUATED_BT}).\n"
        f"Decoded event count: {len(all_events)}. "
        "The overlap window is empty — byte-parity cannot be asserted."
    )


def test_overlap_same_count_in_overlap_window() -> None:
    """Both paths produce the same count of normalized swaps in the overlap window.

    helius_live path filtered to post-grad must equal the simulated birdeye_live
    path's full output count — birdeye subscribes at graduation so all its swaps
    are post-grad.  A count mismatch indicates a normalization divergence.
    """
    raw_rows = _load_fixture_rows()
    all_events = _get_all_decoded_events(raw_rows)
    post_grad_events = _get_post_grad_decoded_events(all_events)

    helius_overlap = _filter_to_overlap(_run_helius_path(raw_rows))
    birdeye_swaps = _run_birdeye_simulation_path(post_grad_events)

    assert len(helius_overlap) > 0, (
        "helius_live produced 0 post-grad swaps in the overlap window — "
        "fixture may be corrupt or filter logic wrong."
    )
    assert len(helius_overlap) == len(birdeye_swaps), (
        f"helius_live overlap count: {len(helius_overlap)}; "
        f"birdeye_live count: {len(birdeye_swaps)}. "
        "Both paths must produce identical swap counts in the overlap window."
    )


def test_overlap_swap_level_byte_identity() -> None:
    """MAIN GATE: all non-source NormalizedSwap fields are byte-identical in the overlap window.

    rel, price, side, vol_sol, vol_usd, sol_usd, owner, block_time, slot, signature,
    base_reserve, quote_reserve, quote_mint, phase, mint — ALL must match between the
    helius_live and simulated birdeye_live paths.  Only the 'source' provenance tag
    may differ (helius_live vs birdeye_live).

    This is the AC-36.1 swap-level byte-identity assertion — the anti-drift contract
    (oracle §3 / #358/#359/#367) folded into the existing US-32 gate.
    """
    raw_rows = _load_fixture_rows()
    all_events = _get_all_decoded_events(raw_rows)
    post_grad_events = _get_post_grad_decoded_events(all_events)

    helius_overlap = _filter_to_overlap(_run_helius_path(raw_rows))
    birdeye_swaps = _run_birdeye_simulation_path(post_grad_events)

    assert len(helius_overlap) == len(birdeye_swaps), (
        "Cannot compare swap-by-swap: paths produced different counts in overlap window."
    )

    for i, (helius, birdeye) in enumerate(zip(helius_overlap, birdeye_swaps)):
        helius_json = _all_non_source_fields_as_json(helius)
        birdeye_json = _all_non_source_fields_as_json(birdeye)
        assert helius_json == birdeye_json, (
            f"Swap[{i}] NOT byte-identical in overlap window "
            f"(AC-36.1 / G2/helius anti-drift VIOLATION):\n"
            f"  helius_live:  {helius_json}\n"
            f"  birdeye_live: {birdeye_json}"
        )


def test_overlap_feature_level_byte_identity() -> None:
    """MAIN GATE: FeatureExtractor.extract_from_lake() output is byte-identical in the overlap window.

    Converts each path's overlap-window NormalizedSwaps to lake-row format and feeds
    them through the US-30 FeatureExtractor (window_s=_FEATURE_WINDOW_S).  Both calls
    must return equal dicts — byte-identical compute_features output.

    The FeatureExtractor (US-30) is the single shared code path (Principle #2);
    byte-identical input → byte-identical output BY CONSTRUCTION.  This is the
    AC-36.1 feature-level byte-identity assertion (anti-drift contract, oracle §3).
    """
    from core.feature_extractor import FeatureExtractor

    raw_rows = _load_fixture_rows()
    all_events = _get_all_decoded_events(raw_rows)
    post_grad_events = _get_post_grad_decoded_events(all_events)

    helius_overlap = _filter_to_overlap(_run_helius_path(raw_rows))
    birdeye_swaps = _run_birdeye_simulation_path(post_grad_events)

    helius_rows = [_swap_to_lake_row(s) for s in helius_overlap]
    birdeye_rows = [_swap_to_lake_row(s) for s in birdeye_swaps]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    helius_features = extractor.extract_from_lake(
        GOLDEN_MINT, helius_rows, window_s=_FEATURE_WINDOW_S
    )
    birdeye_features = extractor.extract_from_lake(
        GOLDEN_MINT, birdeye_rows, window_s=_FEATURE_WINDOW_S
    )

    assert helius_features is not None, (
        "helius_live overlap features are None — no usable post-grad swaps within "
        f"the feature window (window_s={_FEATURE_WINDOW_S}). "
        "Check fixture post-grad rel values vs _FEATURE_WINDOW_S."
    )
    assert birdeye_features is not None, (
        "birdeye_live overlap features are None — no usable post-grad swaps within "
        f"the feature window (window_s={_FEATURE_WINDOW_S})."
    )
    assert helius_features == birdeye_features, (
        "Feature dicts NOT byte-identical in the overlap window "
        f"(AC-36.1 / G2/helius anti-drift VIOLATION).\n"
        f"  helius keys:  {sorted(helius_features)}\n"
        f"  birdeye keys: {sorted(birdeye_features)}\n"
        f"  Differing keys: "
        f"{[k for k in helius_features if helius_features[k] != birdeye_features.get(k)]}"
    )


def test_overlap_source_tags_correct() -> None:
    """helius path: source='helius_live'; birdeye path: source='birdeye_live'.

    The 'source' field is the ONE intentional difference between the two paths.
    Confirms provenance tags are correctly set so the lake retains traceability
    of which source wrote each swap.
    """
    raw_rows = _load_fixture_rows()
    all_events = _get_all_decoded_events(raw_rows)
    post_grad_events = _get_post_grad_decoded_events(all_events)

    helius_overlap = _filter_to_overlap(_run_helius_path(raw_rows))
    birdeye_swaps = _run_birdeye_simulation_path(post_grad_events)

    for i, swap in enumerate(helius_overlap):
        assert swap.source == "helius_live", (
            f"helius overlap Swap[{i}].source={swap.source!r}, expected 'helius_live'."
        )
    for i, swap in enumerate(birdeye_swaps):
        assert swap.source == "birdeye_live", (
            f"birdeye simulation Swap[{i}].source={swap.source!r}, "
            "expected 'birdeye_live'."
        )


def test_overlap_post_grad_swaps_have_positive_rel() -> None:
    """All swaps in the overlap window have rel >= 0 (post-graduation anchoring per §7.1).

    Pre-graduation swaps carry negative rel.  The overlap window contains only
    post-graduation events — both helius_live and Birdeye see the same transactions
    there.  A negative-rel swap in the overlap is a graduation-anchor bug.
    """
    raw_rows = _load_fixture_rows()
    helius_overlap = _filter_to_overlap(_run_helius_path(raw_rows))

    assert len(helius_overlap) > 0, (
        "No post-grad swaps to verify — overlap window is empty."
    )
    for i, swap in enumerate(helius_overlap):
        assert swap.rel >= 0, (
            f"Overlap swap[{i}] has rel={swap.rel} < 0 — it is pre-graduation "
            "and must not appear in the overlap window."
        )


def test_overlap_features_non_none() -> None:
    """compute_features returns non-None — the feature test is not vacuously passing.

    Guards against a degenerate fixture where all overlap swaps fall outside
    the feature window, which would make both paths return None and the
    byte-identity assertion pass trivially on None == None.
    """
    from core.feature_extractor import FeatureExtractor

    raw_rows = _load_fixture_rows()
    all_events = _get_all_decoded_events(raw_rows)
    post_grad_events = _get_post_grad_decoded_events(all_events)
    helius_overlap = _filter_to_overlap(_run_helius_path(raw_rows))
    helius_rows = [_swap_to_lake_row(s) for s in helius_overlap]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    features = extractor.extract_from_lake(
        GOLDEN_MINT, helius_rows, window_s=_FEATURE_WINDOW_S
    )

    assert features is not None, (
        f"FeatureExtractor returned None over the overlap window "
        f"(window_s={_FEATURE_WINDOW_S}, post-grad events={len(post_grad_events)}, "
        f"overlap swaps={len(helius_overlap)}). "
        "The golden fixture must have at least one post-grad swap with rel < _FEATURE_WINDOW_S."
    )
    assert features.get("tape_n_trades", 0) > 0, (
        f"tape_n_trades={features.get('tape_n_trades')} — must be > 0 for real overlap data."
    )
