# ---
# module: core.tests.test_pregrad_self_consistency_ac362
# sprint: sprint-8
# story: US-36 AC-36.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape.helius_birth_tape_source, core.tape.manifest,
#               core.tape.recorder, core.replay_source, core.clock,
#               core.normalized_swap, core.feature_extractor,
#               core.tape_microstructure,
#               tools.helius_birth_tape_activate,
#               core.tests.test_birth_tape_live_fixture_ac343,
#               asyncio, dataclasses, gzip, json, pathlib, types
# ---
"""AC-36.2 — Pre-grad-only raw-truth self-consistency + MANIFEST gate.

For the pre-graduation window (no Birdeye counterpart), the gate is:

  1. Helius decode DETERMINISM — run the decode twice on the banked birth-tape
     fixture → byte-identical normalized swaps both times.

  2. §6.4 schema invariants — every pre-graduation NormalizedSwap satisfies:
       rel < 0 (anchored to graduated_block_time, pre-grad is before grad)
       source ∈ VALID_SOURCES ("helius_live")
       phase  ∈ VALID_PHASES
       side   ∈ VALID_SIDES
       block_time > 0, slot >= 0, signature non-empty, owner non-empty

  3. G1 feature golden-parity — the birth-tape fixture (pre-grad + post-grad
     swaps) fed through the US-30 FeatureExtractor with window_s=250 produces
     byte-identical feature dicts to a frozen golden record.  compute_features
     uses 0 <= rel < window_s, so pre-grad (negative rel) swaps are correctly
     excluded; the gate verifies the full pipeline end-to-end.

  4. MANIFEST — every dataset entering the lake carries a MANIFEST (data-lake.md:
     dataset_id, source, date_range, mint_cohort, row_count, content_hash).
     Cross-machine/cross-run comparisons use LC_ALL=C sort (byte-order) on the
     mint_cohort list.

     _ensure_birth_tape_manifest_exists() generates the MANIFEST.json alongside
     the banked golden fixture if it is absent (fresh clone without the committed
     file) — deterministic and idempotent.  When the file is committed to the
     repo, it is read as-is and verified against the actual fixture.

Tests
-----
  test_pregrad_decode_twice_byte_identical          ← MAIN GATE (decode determinism)
      Raw fixture rows decoded twice → same list of dicts both times.

  test_pregrad_normalized_swaps_twice_identical     ← MAIN GATE (swap determinism)
      TapeRecorder run twice over fixture → byte-identical NormalizedSwap lists.

  test_pregrad_schema_rel_negative
      Every pre-grad NormalizedSwap has rel < 0 (graduated_block_time anchor).

  test_pregrad_schema_source_valid
      Every pre-grad swap has source ∈ core.normalized_swap.VALID_SOURCES.

  test_pregrad_schema_fields_valid
      Every pre-grad swap has block_time > 0, slot >= 0, non-empty signature,
      non-empty owner, side ∈ VALID_SIDES, phase ∈ VALID_PHASES.

  test_pregrad_g1_feature_golden_parity             ← MAIN GATE (G1 parity)
      FeatureExtractor over the full birth-tape fixture (window_s=250) returns
      the frozen golden feature dict within 1e-9 tolerance.

  test_pregrad_g1_features_non_none
      Confirms the feature extraction returns non-None over the birth-tape data.

  test_birth_tape_manifest_exists
      MANIFEST.json is present alongside the birth-tape golden fixture.

  test_birth_tape_manifest_has_required_fields
      MANIFEST contains all required keys from data-lake.md.

  test_birth_tape_manifest_source_is_helius_live
      MANIFEST.source == "helius_live".

  test_birth_tape_manifest_row_count_correct
      MANIFEST.row_count matches the actual number of rows in the fixture.

  test_birth_tape_manifest_content_hash_correct     ← MAIN GATE (integrity)
      MANIFEST.content_hash matches the SHA-256 of the fixture's decompressed
      content — detects any untracked modification to the fixture file.

  test_birth_tape_manifest_mint_cohort_lc_all_c_sorted
      MANIFEST.mint_cohort is LC_ALL=C (byte-order) sorted — cross-machine
      determinism guard.

  test_birth_tape_manifest_date_range_present
      MANIFEST.date_range has non-empty start and end fields.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.normalized_swap import VALID_PHASES, VALID_SIDES, VALID_SOURCES
from core.tape.helius_birth_tape_source import decode_helius_notification
from core.tape.manifest import (
    MANIFEST_REQUIRED_KEYS,
    build_manifest,
    compute_content_hash,
    lc_all_c_sort,
    read_manifest,
    write_manifest,
)

# Import AC-34.3 module-level constants + idempotent fixture creator.
# Importing this module also runs its module-level _ensure_fixture_exists(),
# which creates the committed golden file on fresh clones — idempotent.
from core.tests.test_birth_tape_live_fixture_ac343 import (
    GOLDEN_MINT,
    GRADUATED_BT,
    _ensure_fixture_exists,
)
from tools.helius_birth_tape_activate import load_birth_tape_fixture

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FIXTURE_DATE: str = "2026-06-17"

#: AC-36.2 window — must cover both post-grad swaps (rel=100 and rel=200).
_FEATURE_WINDOW_S: int = 250
_FEATURE_BUCKET_S: int = 15

#: Frozen golden feature values for the birth-tape fixture at window_s=250.
#: Pre-grad swaps (rel < 0) are excluded by compute_features (0 <= rel < window_s).
#: Two post-grad swaps contribute: rel=100 (buy) and rel=200 (sell), price=0.05 each.
#: n_buckets = 250 // 15 = 16  → tape_close_b0 through tape_close_b15.
_BIRTH_TAPE_G1_GOLDEN: dict = {
    "tape_n_trades": 2,
    "tape_n_buy": 1,
    "tape_n_sell": 1,
    "tape_n_unique_traders": 1,
    "tape_vol_total": 1.0,
    "tape_buy_vol": 0.5,
    "tape_sell_vol": 0.5,
    "tape_buy_sell_vol_ratio": 1.0,
    "tape_all_buy_window": 0,
    "tape_sell_frac_cnt": 0.5,
    "tape_ret_total": 0.0,
    "tape_max_runup": 0.0,
    "tape_max_drawdown": 0.0,
    "tape_high_over_open": 0.0,
    "tape_low_over_open": 0.0,
    "tape_logprice_slope_per_s": 0.0,
    "tape_logprice_accel": 0.0,
    "tape_time_to_peak_s": 100.0,
    "tape_time_to_first_sell_s": 200.0,
    "tape_late_bucket_ret": 0.0,
    "tape_n_buckets_active": 2,
    "tape_first_swap_rel_s": 100.0,
    # Bucket-level close ratios: all 1.0 because price is constant (0.05) for
    # all swaps. Buckets with no trades carry the previous bucket's close
    # (forward-fill), which is always 0.05 = open_p → ratio 1.0.
    **{f"tape_close_b{i}": 1.0 for i in range(16)},
}

_FLOAT_TOLERANCE: float = 1e-9

#: FeatureSet stub — provides hash/math_version metadata for FeatureExtractor.
_MOCK_FEATURE_SET = SimpleNamespace(
    hash="ac362-pregrad-self-consistency-mock",
    math_version="solanabilly3:sprint-8",
)

#: Fixed clock anchor for deterministic TapeRecorder replays.
_T0 = datetime(2026, 6, 17, 12, 0, 0, tzinfo=timezone.utc)

#: Token store matching the golden fixture's GRADUATED_BT anchor.
_TOKEN_STORE: dict = {GOLDEN_MINT: SimpleNamespace(graduated_block_time=GRADUATED_BT)}


# ---------------------------------------------------------------------------
# Module-level fixture bootstrap — runs once at import time
# ---------------------------------------------------------------------------

_FIXTURE_PATH: Path = _ensure_fixture_exists()


# ---------------------------------------------------------------------------
# MANIFEST bootstrap — idempotent, deterministic
# ---------------------------------------------------------------------------


def _birth_tape_manifest_dir() -> Path:
    """Return the directory containing the birth-tape golden fixture."""
    return _FIXTURE_PATH.parent


def _ensure_birth_tape_manifest_exists() -> dict:
    """Return the MANIFEST for the birth-tape golden fixture, creating it if absent.

    If MANIFEST.json already exists alongside the fixture (the normal case for a
    repo clone with committed files), it is read and returned as-is.

    If it is missing (fresh clone where only the fixture was reconstructed, or
    first-run before the MANIFEST was committed), it is generated deterministically
    from the fixture file and the known golden constants, then written to disk.

    Returns:
        The MANIFEST dict (either read from disk or freshly generated).
    """
    manifest_file = _birth_tape_manifest_dir() / "MANIFEST.json"
    if manifest_file.exists():
        return read_manifest(_birth_tape_manifest_dir())

    # Generate from fixture contents
    rows = load_birth_tape_fixture(_FIXTURE_PATH)
    content_hash = compute_content_hash(_FIXTURE_PATH)

    manifest = build_manifest(
        dataset_id=f"helius_birth_tape_pregrad_golden_dt{_FIXTURE_DATE}",
        source="helius_live",
        date_start=_FIXTURE_DATE,
        date_end=_FIXTURE_DATE,
        mint_cohort=[GOLDEN_MINT],
        row_count=len(rows),
        content_hash=content_hash,
    )
    write_manifest(_birth_tape_manifest_dir(), manifest)
    return manifest


_BIRTH_TAPE_MANIFEST: dict = _ensure_birth_tape_manifest_exists()


# ---------------------------------------------------------------------------
# Helpers — fixture I/O and recorder runner
# ---------------------------------------------------------------------------


def _load_fixture_rows() -> list[dict]:
    """Load the AC-34.3 golden birth-tape fixture rows from disk."""
    return load_birth_tape_fixture(_FIXTURE_PATH)


def _decode_all(rows: list[dict]) -> list[dict | None]:
    """Decode all raw notification rows (None for migrate/malformed events)."""
    return [decode_helius_notification(r) for r in rows]


def _run_recorder(rows: list[dict]) -> list:
    """Run the full TapeRecorder pipeline over raw fixture rows → NormalizedSwaps."""
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.mapped_source import MappedSwapSource
    from core.tape.recorder import TapeRecorder

    async def _inner():
        source = MappedSwapSource(
            ReplaySource(event_log=rows),
            decode_helius_notification,
        )
        recorder = TapeRecorder(
            source=source,
            clock=VirtualClock(_T0),
            token_store=_TOKEN_STORE,
            swap_source="helius_live",
            swap_phase="pre",
        )
        await recorder.run()
        return recorder.normalized_swaps

    return asyncio.run(_inner())


def _swap_as_comparable_dict(swap) -> dict:
    """Serialize a NormalizedSwap as a plain dict for byte-identity comparison."""
    return dataclasses.asdict(swap)


def _get_pregrad_swaps(swaps: list) -> list:
    """Filter NormalizedSwaps to the pre-grad window (rel < 0)."""
    return [s for s in swaps if s.rel < 0]


# ---------------------------------------------------------------------------
# Tests — Decode determinism (§ "Helius decode DETERMINISM")
# ---------------------------------------------------------------------------


def test_pregrad_decode_twice_byte_identical() -> None:
    """MAIN GATE: raw fixture rows decoded twice → byte-identical results.

    Runs decode_helius_notification over every row of the birth-tape golden
    fixture on two separate passes.  Both passes must produce identical output
    lists — same length, same dict contents for each decoded event, same None
    positions for non-trade events (migrate, malformed).

    This is the AC-36.2 decode-determinism assertion for the pre-grad window.
    The birth-tape decode path has no randomness or I/O; this test proves it.
    """
    rows = _load_fixture_rows()
    first_pass = _decode_all(rows)
    second_pass = _decode_all(rows)

    assert first_pass == second_pass, (
        "Helius decode is NOT deterministic over the birth-tape fixture "
        "(AC-36.2 / VIOLATION): the two decode passes produced different outputs.\n"
        f"  First  pass length:  {len(first_pass)}\n"
        f"  Second pass length:  {len(second_pass)}\n"
        f"  Differing indices:   "
        f"{[i for i, (a, b) in enumerate(zip(first_pass, second_pass)) if a != b]}"
    )


def test_pregrad_normalized_swaps_twice_identical() -> None:
    """MAIN GATE: TapeRecorder run twice over fixture → byte-identical NormalizedSwaps.

    Runs the full decode → MappedSwapSource → TapeRecorder pipeline twice and
    asserts that the resulting NormalizedSwap lists are byte-identical.  Covers
    all normalization, rel-anchoring, and source-tagging logic.
    """
    rows = _load_fixture_rows()
    first_run = _run_recorder(rows)
    second_run = _run_recorder(rows)

    assert len(first_run) == len(second_run), (
        f"TapeRecorder run count differs: {len(first_run)} vs {len(second_run)} "
        "(AC-36.2 swap determinism VIOLATION)."
    )

    for i, (s1, s2) in enumerate(zip(first_run, second_run)):
        d1 = _swap_as_comparable_dict(s1)
        d2 = _swap_as_comparable_dict(s2)
        assert d1 == d2, (
            f"NormalizedSwap[{i}] NOT byte-identical between runs "
            f"(AC-36.2 swap determinism VIOLATION):\n"
            f"  Run 1: {d1}\n"
            f"  Run 2: {d2}"
        )


# ---------------------------------------------------------------------------
# Tests — §6.4 schema invariants for pre-graduation swaps
# ---------------------------------------------------------------------------


def test_pregrad_schema_rel_negative() -> None:
    """Every pre-grad NormalizedSwap must have rel < 0 (graduated_block_time anchor).

    Pre-graduation swaps occur BEFORE the token graduates.  Their block_time is
    less than graduated_block_time (the DB anchor for rel), so rel must be
    strictly negative.  A rel >= 0 on a pre-grad swap indicates an anchoring bug.
    """
    rows = _load_fixture_rows()
    all_swaps = _run_recorder(rows)
    pregrad = _get_pregrad_swaps(all_swaps)

    assert len(pregrad) > 0, (
        "No pre-grad swaps found — fixture may be missing pre-graduation trade rows."
    )

    for i, swap in enumerate(pregrad):
        assert swap.rel < 0, (
            f"Pre-grad swap[{i}] has rel={swap.rel} >= 0 "
            f"(block_time={swap.block_time}, GRADUATED_BT={GRADUATED_BT}) — "
            "§6.4 invariant VIOLATION: pre-grad rel must be < 0."
        )


def test_pregrad_schema_source_valid() -> None:
    """Every pre-grad swap has source ∈ VALID_SOURCES (PRD §7.1, AC-34.1).

    The birth-tape source tags all swaps as 'helius_live'.  This test verifies
    that no swap slips through with an unknown source tag.
    """
    rows = _load_fixture_rows()
    all_swaps = _run_recorder(rows)
    pregrad = _get_pregrad_swaps(all_swaps)

    for i, swap in enumerate(pregrad):
        assert swap.source in VALID_SOURCES, (
            f"Pre-grad swap[{i}].source={swap.source!r} not in VALID_SOURCES "
            f"{sorted(VALID_SOURCES)} — §6.4 schema VIOLATION."
        )
        assert swap.source == "helius_live", (
            f"Pre-grad swap[{i}].source={swap.source!r}, expected 'helius_live' "
            "for the birth-tape source."
        )


def test_pregrad_schema_fields_valid() -> None:
    """Every pre-grad swap satisfies the §6.4 field-level invariants.

    Checks:
      block_time > 0         (valid Unix timestamp)
      block_time < GRADUATED_BT   (strictly before graduation)
      slot       >= 0        (non-negative slot number)
      signature  non-empty   (the on-chain transaction signature)
      owner      non-empty   (the signer wallet)
      side       ∈ VALID_SIDES
      phase      ∈ VALID_PHASES
      mint       == GOLDEN_MINT  (self-describing lake row per AC-34.2)
    """
    rows = _load_fixture_rows()
    all_swaps = _run_recorder(rows)
    pregrad = _get_pregrad_swaps(all_swaps)

    assert len(pregrad) > 0, "No pre-grad swaps — fixture is missing pre-grad trades."

    for i, swap in enumerate(pregrad):
        ctx = f"Pre-grad swap[{i}] (block_time={swap.block_time})"
        assert swap.block_time > 0, f"{ctx}: block_time={swap.block_time} must be > 0."
        assert swap.block_time < GRADUATED_BT, (
            f"{ctx}: block_time={swap.block_time} must be < GRADUATED_BT={GRADUATED_BT}."
        )
        assert swap.slot >= 0, f"{ctx}: slot={swap.slot} must be >= 0."
        assert swap.signature, f"{ctx}: signature is empty — §6.4 schema VIOLATION."
        assert swap.owner, f"{ctx}: owner is empty — §6.4 schema VIOLATION."
        assert swap.side in VALID_SIDES, (
            f"{ctx}: side={swap.side!r} not in VALID_SIDES {sorted(VALID_SIDES)}."
        )
        assert swap.phase in VALID_PHASES, (
            f"{ctx}: phase={swap.phase!r} not in VALID_PHASES {sorted(VALID_PHASES)}."
        )
        assert swap.mint == GOLDEN_MINT, (
            f"{ctx}: mint={swap.mint!r} != GOLDEN_MINT={GOLDEN_MINT!r} — "
            "AC-34.2 self-describing lake row VIOLATION."
        )


# ---------------------------------------------------------------------------
# Tests — G1 feature golden-parity over the birth-tape fixture
# ---------------------------------------------------------------------------


def test_pregrad_g1_features_non_none() -> None:
    """FeatureExtractor returns non-None over the birth-tape fixture (non-vacuous guard).

    Guards against a degenerate fixture where all swaps have rel outside the
    feature window, which would make the extractor return None and the golden
    parity assertion pass trivially on None == None.

    The birth-tape fixture has post-grad swaps at rel=100 and rel=200, both
    within window_s=250, so the extractor must return a non-None dict.
    """
    from core.feature_extractor import FeatureExtractor

    rows = _load_fixture_rows()
    all_swaps = _run_recorder(rows)
    lake_rows = [s.to_dict() for s in all_swaps]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    features = extractor.extract_from_lake(
        GOLDEN_MINT, lake_rows, window_s=_FEATURE_WINDOW_S
    )

    assert features is not None, (
        f"FeatureExtractor returned None over the birth-tape fixture "
        f"(window_s={_FEATURE_WINDOW_S}). "
        "The fixture must have at least one post-grad swap with 0 <= rel < window_s."
    )
    assert features.get("tape_n_trades", 0) > 0, (
        f"tape_n_trades={features.get('tape_n_trades')} — must be > 0 for a "
        "non-degenerate fixture."
    )


def test_pregrad_g1_feature_golden_parity() -> None:
    """MAIN GATE: features from the birth-tape fixture match the frozen golden record.

    Runs the full birth-tape fixture through the US-30 FeatureExtractor with
    window_s=250 and compares every feature against _BIRTH_TAPE_G1_GOLDEN within
    _FLOAT_TOLERANCE (1e-9).  Pre-grad swaps (rel < 0) are correctly excluded by
    compute_features (0 <= rel < window_s), so this test proves the end-to-end
    pipeline is deterministic and produces the expected feature values.

    This is the AC-36.2 G1 feature golden-parity assertion.  The frozen golden
    values were derived analytically from the known-fixed birth-tape inputs:
      - 2 post-grad swaps: rel=100 (buy), rel=200 (sell), price=0.05, vol=0.5
      - Same owner (GOLDEN_USER) for both → tape_n_unique_traders = 1
      - Constant price → all return/drawdown/slope features = 0.0
      - bucket_s=15, window_s=250 → 16 buckets, active in buckets 6 and 13
    """
    from core.feature_extractor import FeatureExtractor

    rows = _load_fixture_rows()
    all_swaps = _run_recorder(rows)
    lake_rows = [s.to_dict() for s in all_swaps]

    extractor = FeatureExtractor(_MOCK_FEATURE_SET)
    features = extractor.extract_from_lake(
        GOLDEN_MINT, lake_rows, window_s=_FEATURE_WINDOW_S
    )

    assert features is not None, (
        "FeatureExtractor returned None — no usable post-grad swaps in the feature window."
    )

    # Verify all expected golden keys are present
    missing_keys = [k for k in _BIRTH_TAPE_G1_GOLDEN if k not in features]
    assert not missing_keys, (
        f"Feature dict missing golden keys: {missing_keys}\n"
        "The vendored compute_features may have changed its output schema."
    )

    # Verify all golden values match within tolerance
    failures: list[str] = []
    for key, expected in _BIRTH_TAPE_G1_GOLDEN.items():
        actual = features[key]
        if isinstance(expected, int) and isinstance(actual, int):
            if actual != expected:
                failures.append(
                    f"  {key}: expected={expected!r}, actual={actual!r} (exact int mismatch)"
                )
        else:
            delta = abs(float(actual) - float(expected))
            if delta > _FLOAT_TOLERANCE:
                failures.append(
                    f"  {key}: expected={expected}, actual={actual}, "
                    f"delta={delta:.2e} > tolerance={_FLOAT_TOLERANCE:.2e}"
                )

    assert not failures, (
        "Birth-tape G1 feature golden-parity FAILED "
        "(AC-36.2 / G1 parity VIOLATION):\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Tests — MANIFEST existence and correctness
# ---------------------------------------------------------------------------


def test_birth_tape_manifest_exists() -> None:
    """MANIFEST.json is present alongside the birth-tape golden fixture.

    Verifies that _ensure_birth_tape_manifest_exists() created or found
    MANIFEST.json at the expected path.  A missing MANIFEST indicates the
    dataset entered the lake without the required metadata (data-lake.md §1).
    """
    manifest_file = _birth_tape_manifest_dir() / "MANIFEST.json"
    assert manifest_file.exists(), (
        f"MANIFEST.json missing at {manifest_file}\n"
        "Every dataset entering the lake must carry a MANIFEST (AC-36.2 / data-lake.md)."
    )


def test_birth_tape_manifest_has_required_fields() -> None:
    """MANIFEST contains all required keys from data-lake.md.

    Required: dataset_id, source, date_range, mint_cohort, row_count, content_hash.
    A MANIFEST missing any of these fields cannot be used for cross-machine or
    cross-run dataset comparisons.
    """
    missing = MANIFEST_REQUIRED_KEYS - set(_BIRTH_TAPE_MANIFEST.keys())
    assert not missing, (
        f"MANIFEST is missing required fields: {sorted(missing)}\n"
        "AC-36.2 requires all data-lake.md MANIFEST fields to be present."
    )


def test_birth_tape_manifest_source_is_helius_live() -> None:
    """MANIFEST.source == 'helius_live' — the birth-tape source tag."""
    assert _BIRTH_TAPE_MANIFEST["source"] == "helius_live", (
        f"MANIFEST.source={_BIRTH_TAPE_MANIFEST['source']!r}, expected 'helius_live'."
    )


def test_birth_tape_manifest_row_count_correct() -> None:
    """MANIFEST.row_count matches the actual number of rows in the fixture file.

    Loads the fixture and counts the rows, then asserts equality with the
    committed MANIFEST.row_count.  A mismatch means the MANIFEST was not
    updated when the fixture was modified.
    """
    rows = _load_fixture_rows()
    actual_count = len(rows)
    manifest_count = _BIRTH_TAPE_MANIFEST["row_count"]
    assert actual_count == manifest_count, (
        f"MANIFEST.row_count={manifest_count} does not match actual fixture rows "
        f"{actual_count} (AC-36.2 / MANIFEST integrity VIOLATION).\n"
        f"The MANIFEST was not updated when the fixture was changed."
    )


def test_birth_tape_manifest_content_hash_correct() -> None:
    """MAIN GATE: MANIFEST.content_hash matches SHA-256 of fixture's decompressed content.

    Detects any untracked modification to the birth-tape golden fixture file.
    The hash is over the DECOMPRESSED bytes of the .jsonl.gz file (not the
    compressed container) so different gzip compression settings across machines
    do not produce false mismatches.
    """
    actual_hash = compute_content_hash(_FIXTURE_PATH)
    manifest_hash = _BIRTH_TAPE_MANIFEST["content_hash"]
    assert actual_hash == manifest_hash, (
        f"Birth-tape fixture content hash mismatch "
        f"(AC-36.2 / MANIFEST integrity VIOLATION):\n"
        f"  MANIFEST:  {manifest_hash}\n"
        f"  Actual:    {actual_hash}\n"
        f"  File: {_FIXTURE_PATH}\n"
        "The fixture was modified without updating the MANIFEST."
    )


def test_birth_tape_manifest_mint_cohort_lc_all_c_sorted() -> None:
    """MANIFEST.mint_cohort is LC_ALL=C (byte-order ASCII) sorted.

    Cross-machine comparisons of MANIFEST files must use the same sort order
    on both macOS (ICU) and Linux (glibc).  Byte-order (ASCII) sort is the
    cross-platform standard matching `LC_ALL=C sort`.  This test verifies the
    committed MANIFEST was built with lc_all_c_sort().
    """
    cohort = _BIRTH_TAPE_MANIFEST["mint_cohort"]
    assert isinstance(cohort, list), f"mint_cohort must be a list, got {type(cohort)}"

    expected_sorted = lc_all_c_sort(cohort)
    assert cohort == expected_sorted, (
        "MANIFEST.mint_cohort is NOT LC_ALL=C sorted "
        "(AC-36.2 cross-machine sort VIOLATION):\n"
        f"  stored:   {cohort}\n"
        f"  expected: {expected_sorted}"
    )


def test_birth_tape_manifest_date_range_present() -> None:
    """MANIFEST.date_range has non-empty start and end fields."""
    date_range = _BIRTH_TAPE_MANIFEST.get("date_range", {})
    assert isinstance(date_range, dict), (
        f"MANIFEST.date_range must be a dict, got {type(date_range)}"
    )
    assert date_range.get("start"), "MANIFEST.date_range.start is empty."
    assert date_range.get("end"), "MANIFEST.date_range.end is empty."


def test_birth_tape_manifest_mint_cohort_contains_golden_mint() -> None:
    """MANIFEST.mint_cohort contains GOLDEN_MINT — the fixture's only mint."""
    cohort = _BIRTH_TAPE_MANIFEST["mint_cohort"]
    assert GOLDEN_MINT in cohort, (
        f"MANIFEST.mint_cohort does not contain GOLDEN_MINT={GOLDEN_MINT!r}.\n"
        f"  cohort: {cohort}"
    )
