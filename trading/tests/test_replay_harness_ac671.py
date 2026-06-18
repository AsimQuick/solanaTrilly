# ---
# module: trading.tests.test_replay_harness_ac671
# sprint: sprint-13
# story: US-67 AC-67.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.replay_harness, trading.models, trading.schemas, pytest, json
# ---
"""AC-67.1 — T2 full-pipeline replay harness ('a day in 30s').

Verifies that DayReplayHarness:
  1. Produces Position/PnL rows deterministically (run-twice-identical).
  2. Matches the pinned golden fixture exactly (any diff is a CI failure).
  3. Writes to the isolated sandbox (ReplayPosition, 'trading_replay_positions')
     and never touches the live Position table ('trading_positions').
  4. Correctly identifies enterable vs un-enterable tokens.
  5. Harness is offline — no network, no firehose (DataSource=Replay).

Golden fixture: trading/tests/fixtures/replay_golden_ac671.json
Tape fixture:   trading/tests/fixtures/replay_tape_day_ac671.json
Source tape:    solanatrills/lake/tapes/2026-06-12.parquet (top-5 mints)

Test sections
-------------
  §1  Golden fixture match
        test_run_matches_golden_fixture

  §2  Run-twice-identical determinism
        test_run_twice_identical

  §3  Sandbox isolation (no live table writes)
        test_writes_to_sandbox_not_live_table
        test_replay_position_rows_created

  §4  Result structure
        test_enterable_tokens_have_required_fields
        test_all_fixture_tokens_enterable
        test_sorted_mint_order

  §5  Harness configuration
        test_score_threshold_filters_tokens
        test_custom_predictor_used
        test_replay_run_id_stamped_on_rows

All tests are offline/deterministic — no network, no Birdeye, no Helius.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_TAPE_FIXTURE = _FIXTURES_DIR / "replay_tape_day_ac671.json"
_GOLDEN_FIXTURE = _FIXTURES_DIR / "replay_golden_ac671.json"


def _load_tape() -> dict:
    """Load the banked multi-token tape day fixture."""
    with open(_TAPE_FIXTURE) as f:
        data = json.load(f)
    return data


def _load_golden() -> dict:
    """Load the pinned golden fixture."""
    with open(_GOLDEN_FIXTURE) as f:
        return json.load(f)


def _results_to_json(results: list[dict]) -> str:
    """Serialise harness results to canonical JSON for exact comparison."""
    return json.dumps(results, sort_keys=True)


def _make_harness(predictor=None, score_threshold=0.5, score_delay_s=30.0):
    """Build a DayReplayHarness with default TradingConfig."""
    from trading.replay_harness import DayReplayHarness
    from trading.schemas import TradingConfig

    config = TradingConfig()
    return DayReplayHarness(
        config=config,
        predictor=predictor,
        score_threshold=score_threshold,
        score_delay_s=score_delay_s,
        size_sol=0.1,
        sol_usd=140.0,
    )


# ---------------------------------------------------------------------------
# §1  Golden fixture match
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_run_matches_golden_fixture():
    """Harness output over the banked tape day must exactly match the pinned golden.

    This is the primary AC-67.1 regression guard: any code change that alters
    the replay output will fail this test.  A diff against the golden file is
    a first-class CI failure.
    """
    tape_data = _load_tape()
    golden = _load_golden()

    harness = _make_harness()
    actual = harness.run(
        tape_data["tape"],
        replay_run_id="golden-verify",
    )

    actual_json = _results_to_json(actual)
    expected_json = _results_to_json(golden["results"])

    assert actual_json == expected_json, (
        "Replay output does not match pinned golden fixture "
        "(trading/tests/fixtures/replay_golden_ac671.json).\n"
        "If this change is intentional, regenerate the golden fixture and "
        "commit it with the code change."
    )


# ---------------------------------------------------------------------------
# §2  Run-twice-identical determinism
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_run_twice_identical():
    """Two independent runs of the harness over the same tape produce identical results.

    Core AC-67.1 requirement: run-twice-identical output.  Any non-determinism
    in the harness (ordering, float instability, state leakage) will fail this.
    """
    tape_data = _load_tape()
    harness = _make_harness()

    result_a = harness.run(tape_data["tape"], replay_run_id="run-a")
    result_b = harness.run(tape_data["tape"], replay_run_id="run-b")

    assert _results_to_json(result_a) == _results_to_json(result_b), (
        "Two consecutive replay runs produced different output — "
        "harness is not deterministic."
    )


# ---------------------------------------------------------------------------
# §3  Sandbox isolation (no live table writes)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_writes_to_sandbox_not_live_table():
    """Replay NEVER writes to the live Position table ('trading_positions').

    §11.3 isolation invariant: the harness writes to ReplayPosition
    ('trading_replay_positions') only.  Any regression that causes it to
    write to the live table will fail this test.
    """
    from trading.models import Position

    tape_data = _load_tape()
    harness = _make_harness()

    live_count_before = Position.objects.count()
    harness.run(tape_data["tape"], replay_run_id="isolation-check")
    live_count_after = Position.objects.count()

    assert live_count_before == live_count_after, (
        f"Replay wrote {live_count_after - live_count_before} row(s) to the "
        "live Position table ('trading_positions'). "
        "Replay must ONLY write to 'trading_replay_positions' (§11.3)."
    )


@pytest.mark.django_db
def test_replay_position_rows_created():
    """Harness writes ReplayPosition rows to the isolated sandbox table."""
    from trading.models import ReplayPosition

    tape_data = _load_tape()
    harness = _make_harness()
    run_id = "sandbox-rows-check"

    results = harness.run(tape_data["tape"], replay_run_id=run_id)

    sandbox_count = ReplayPosition.objects.filter(replay_run_id=run_id).count()
    assert sandbox_count == len(results), (
        f"Expected {len(results)} ReplayPosition row(s) for run_id={run_id!r}, "
        f"found {sandbox_count}."
    )
    assert sandbox_count > 0, "Harness must write at least one sandbox row"


# ---------------------------------------------------------------------------
# §4  Result structure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_enterable_tokens_have_required_fields():
    """Every enterable result dict contains the required settlement fields."""
    tape_data = _load_tape()
    harness = _make_harness()
    results = harness.run(tape_data["tape"], replay_run_id="field-check")

    required_fields = {"mint", "score", "enterable", "pnl", "trigger", "held", "peak", "flow"}
    for rec in results:
        if rec.get("enterable"):
            missing = required_fields - set(rec.keys())
            assert not missing, (
                f"Enterable result for mint={rec['mint']!r} is missing fields: {missing}"
            )


@pytest.mark.django_db
def test_all_fixture_tokens_enterable():
    """All 5 tokens in the banked tape day are enterable (pinned by golden)."""
    golden = _load_golden()
    for rec in golden["results"]:
        assert rec["enterable"] is True, (
            f"Expected all golden tokens to be enterable, "
            f"but mint={rec['mint']!r} has enterable={rec['enterable']!r}"
        )


@pytest.mark.django_db
def test_sorted_mint_order():
    """Results are returned in sorted(mint) order — deterministic iteration."""
    tape_data = _load_tape()
    harness = _make_harness()
    results = harness.run(tape_data["tape"], replay_run_id="sort-check")

    mints = [r["mint"] for r in results]
    assert mints == sorted(mints), (
        "Replay results are not in sorted(mint) order — harness is not deterministic"
    )


# ---------------------------------------------------------------------------
# §5  Harness configuration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_score_threshold_filters_tokens():
    """Tokens below score_threshold are excluded from results and sandbox rows."""
    from trading.models import ReplayPosition

    tape_data = _load_tape()

    run_id = "threshold-block-all"
    # Predictor always returns 0.0; threshold is 0.5 → nothing qualifies
    harness = _make_harness(predictor=lambda m, t: 0.0, score_threshold=0.5)
    results = harness.run(tape_data["tape"], replay_run_id=run_id)

    assert len(results) == 0, (
        "All tokens score 0.0; none should qualify at score_threshold=0.5"
    )
    assert ReplayPosition.objects.filter(replay_run_id=run_id).count() == 0, (
        "No sandbox rows should be written when no token qualifies"
    )


@pytest.mark.django_db
def test_custom_predictor_used():
    """The injected predictor is actually called and its score appears in results."""
    tape_data = _load_tape()
    scores_seen = {}

    def tracking_predictor(mint: str, trades) -> float:
        score = 0.9  # fixed non-default score
        scores_seen[mint] = score
        return score

    harness = _make_harness(predictor=tracking_predictor, score_threshold=0.5)
    results = harness.run(tape_data["tape"], replay_run_id="predictor-check")

    assert len(results) > 0, "At least one token should qualify with score=0.9"
    for rec in results:
        assert rec["score"] == pytest.approx(0.9), (
            f"Expected score=0.9 from custom predictor, got {rec['score']!r}"
        )
    assert len(scores_seen) == len(tape_data["tape"]), (
        "Predictor must be called for every mint in the tape"
    )


@pytest.mark.django_db
def test_replay_run_id_stamped_on_rows():
    """Every ReplayPosition row carries the correct replay_run_id."""
    from trading.models import ReplayPosition

    tape_data = _load_tape()
    harness = _make_harness()
    run_id = "stamp-check-12345"

    harness.run(tape_data["tape"], replay_run_id=run_id)

    rows = ReplayPosition.objects.filter(replay_run_id=run_id)
    assert rows.exists(), f"No ReplayPosition rows found for replay_run_id={run_id!r}"
    for row in rows:
        assert row.replay_run_id == run_id, (
            f"Row {row.pk} has replay_run_id={row.replay_run_id!r}, expected {run_id!r}"
        )
