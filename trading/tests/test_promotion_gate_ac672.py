# ---
# module: trading.tests.test_promotion_gate_ac672
# sprint: sprint-13
# story: US-67 AC-67.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: trading.replay_harness, trading.tape_settler, trading.schemas, pytest, json
# ---
"""AC-67.2 — T0+T1+T2 promotion gate + T3 regression corpus.

§1  Parity gate (T0+T1+T2 wired as the promotion gate, §11.2)
      The PnL replay produces AGREES EXACTLY with the static pinned labs-side
      reference (parity_gate_ac672.json).  Any gap is a parity/leak bug.
      The labs-side PnL is NOT dynamically recomputed at test time — a
      dynamically-recomputed reference cannot catch regressions.

§2  T3 verdict corpus — anti-flip guard
      A frozen tape-settled cohort (t3_cohort_settled.json) in CI:
      any code change that flips a verdict (trigger, enterable, pnl) causes
      CI failure.  The #404 file+test-dropped-together evasion is blocked by
      test_t3_corpus_file_present — a separate presence assertion that survives
      deletion of the per-verdict tests (it lives in this same file, which is
      itself tracked by the CI 'test' job and cannot be dropped silently without
      also removing every other test in the project, which would trivially fail
      the 80% coverage gate).

Test sections
-------------
  §1  Parity gate
        test_parity_gate_labs_reference_is_static_and_populated
        test_parity_gate_pnl_agrees_with_labs_reference

  §2  T3 corpus — anti-flip guard
        test_t3_corpus_file_present
        test_t3_corpus_minimum_count
        test_t3_corpus_verdicts_not_flipped

All tests are offline/deterministic — no network, no Birdeye, no Helius.
The parity gate test uses DayReplayHarness (writes ReplayPosition rows, needs DB).
The T3 corpus verdict tests use simulate_tape_exit directly (no DB needed).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).parent / "fixtures"
_CORPUS_DIR = Path(__file__).parent / "corpus"

_TAPE_FIXTURE = _FIXTURES_DIR / "replay_tape_day_ac671.json"
_PARITY_FIXTURE = _FIXTURES_DIR / "parity_gate_ac672.json"
_T3_CORPUS = _CORPUS_DIR / "t3_cohort_settled.json"

# The corpus file whose presence is the #404-class prevention guard.
# Must not be deleted without also deleting this assertion.
_REQUIRED_CORPUS_FILE = "t3_cohort_settled.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_tape() -> dict:
    with open(_TAPE_FIXTURE) as f:
        return json.load(f)


def _load_parity() -> dict:
    with open(_PARITY_FIXTURE) as f:
        return json.load(f)


def _load_t3_corpus() -> dict:
    with open(_T3_CORPUS) as f:
        return json.load(f)


def _make_harness(parity: dict):
    """Build DayReplayHarness from the parity fixture config."""
    from trading.replay_harness import DayReplayHarness
    from trading.schemas import TradingConfig

    cfg = parity["config"]
    config = TradingConfig(
        take_profit_pct=cfg["take_profit_pct"],
        stop_loss_pct=cfg["stop_loss_pct"],
        disaster_cap_pct=cfg["disaster_cap_pct"],
        rug_pull_drop_pct=cfg["rug_pull_drop_pct"],
        auto_sell_timer_s=cfg["auto_sell_timer_s"],
    )
    return DayReplayHarness(
        config=config,
        predictor=None,  # constant 1.0 — all tokens qualify
        score_threshold=0.5,
        score_delay_s=parity["score_delay_s"],
        size_sol=parity["size_sol"],
        sol_usd=parity["sol_usd"],
    )


def _make_config_from_corpus(corpus: dict):
    """Build TradingConfig from the T3 corpus config block."""
    from trading.schemas import TradingConfig

    cfg = corpus["config"]
    return TradingConfig(
        take_profit_pct=cfg["take_profit_pct"],
        stop_loss_pct=cfg["stop_loss_pct"],
        disaster_cap_pct=cfg["disaster_cap_pct"],
        rug_pull_drop_pct=cfg["rug_pull_drop_pct"],
        auto_sell_timer_s=cfg["auto_sell_timer_s"],
    )


# ---------------------------------------------------------------------------
# §1  Parity gate (T0+T1+T2 promotion gate — §11.2)
# ---------------------------------------------------------------------------


def test_parity_gate_labs_reference_is_static_and_populated():
    """The labs-side PnL reference is a static fixture file, not computed at runtime.

    Asserts parity_gate_ac672.json exists, contains a non-empty labs_pnl list, and
    is marked with the required schema key.  This guards against the fixture being
    accidentally cleared or emptied, which would make the parity assertion vacuously
    pass on an empty list.
    """
    assert _PARITY_FIXTURE.exists(), (
        "Missing labs-side parity fixture: "
        "trading/tests/fixtures/parity_gate_ac672.json"
    )
    parity = _load_parity()
    assert "_schema" in parity, "Parity fixture missing _schema key"
    assert "labs_pnl" in parity, "Parity fixture missing labs_pnl key"
    labs = parity["labs_pnl"]
    assert isinstance(labs, list) and len(labs) >= 5, (
        f"labs_pnl must have >= 5 entries (got {len(labs)}); "
        "a truncated reference would make the parity assertion vacuously pass"
    )
    for entry in labs:
        assert "mint" in entry and "pnl" in entry and "trigger" in entry, (
            f"labs_pnl entry {entry} is missing required fields (mint, pnl, trigger)"
        )


@pytest.mark.django_db
def test_parity_gate_pnl_agrees_with_labs_reference():
    """Replay PnL MUST exactly equal the static pinned labs-side reference.

    This is the T0+T1+T2 promotion gate (AC-67.2 / §11.2).  The labs-side PnL
    in parity_gate_ac672.json was computed by the verbatim port of the trills
    oracle (simulate_tape_exit) and pinned at sprint-13 inception.  It is NOT
    recomputed at test time — this is intentional: dynamic recomputation cannot
    catch regressions in the settler math.

    Any divergence between the harness output and the static reference is a
    parity/leak bug that must be fixed before promotion.
    """
    parity = _load_parity()
    tape_data = _load_tape()

    # Keyed by mint for O(1) lookup
    labs_by_mint = {r["mint"]: r for r in parity["labs_pnl"]}
    labs_mints = set(labs_by_mint.keys())

    harness = _make_harness(parity)
    actual = harness.run(tape_data["tape"], replay_run_id="parity-gate-check")

    harness_mints = {r["mint"] for r in actual}

    # Coverage: harness must produce results for exactly the labs mints
    assert harness_mints == labs_mints, (
        f"Mint set mismatch between harness and labs reference.\n"
        f"  Harness only: {harness_mints - labs_mints}\n"
        f"  Labs only:    {labs_mints - harness_mints}"
    )

    for result in actual:
        mint = result["mint"]
        labs = labs_by_mint[mint]

        # enterable must agree
        assert result["enterable"] == labs["enterable"], (
            f"[PARITY BUG] enterable mismatch for {mint}: "
            f"replay={result['enterable']!r}, labs={labs['enterable']!r}"
        )

        if not result["enterable"]:
            continue  # un-enterable: no PnL to compare

        # PnL must match exactly (any epsilon is a leak)
        assert result["pnl"] == labs["pnl"], (
            f"[PARITY BUG] PnL mismatch for {mint}: "
            f"replay={result['pnl']}, labs={labs['pnl']} "
            f"(delta={result['pnl'] - labs['pnl']})"
        )

        # Trigger (verdict) must match exactly
        assert result["trigger"] == labs["trigger"], (
            f"[PARITY BUG] trigger mismatch for {mint}: "
            f"replay={result['trigger']!r}, labs={labs['trigger']!r}"
        )

        # Peak and flow are additional parity signals
        assert result["peak"] == labs["peak"], (
            f"[PARITY BUG] peak mismatch for {mint}: "
            f"replay={result['peak']}, labs={labs['peak']}"
        )
        assert result["flow"] == labs["flow"], (
            f"[PARITY BUG] flow mismatch for {mint}: "
            f"replay={result['flow']}, labs={labs['flow']}"
        )


# ---------------------------------------------------------------------------
# §2  T3 verdict corpus — anti-flip guard
# ---------------------------------------------------------------------------


def test_t3_corpus_file_present():
    """The T3 corpus file must exist in the corpus directory.

    This is the primary #404-class prevention guard: even if the per-verdict
    tests (test_t3_corpus_verdicts_not_flipped) are deleted alongside the corpus
    file, THIS assertion — which is independent and lives in a separate test
    function — will fail CI when the file is missing.

    The guard works because:
      1. The corpus file name is hard-coded here as a string literal.
      2. This test function is distinct from the per-verdict tests.
      3. Dropping this function AND the corpus file simultaneously is a
         two-file edit that is visible in code review and git diff.
    """
    corpus_path = _CORPUS_DIR / _REQUIRED_CORPUS_FILE
    assert corpus_path.exists(), (
        f"Required T3 corpus file is missing: "
        f"trading/tests/corpus/{_REQUIRED_CORPUS_FILE}\n"
        "This file must not be deleted — it is the frozen verdict reference for "
        "the T3 regression corpus (AC-67.2).  A missing file = CI failure."
    )


def test_t3_corpus_minimum_count():
    """The T3 corpus must contain >= expected_count entries.

    Prevents silent truncation of the cohort: if someone strips entries from
    the corpus file (rather than deleting the file), this assertion fails.
    The expected_count field in the corpus is the authoritative minimum.
    """
    corpus = _load_t3_corpus()
    expected = corpus.get("expected_count", 0)
    actual = len(corpus.get("cohort", []))
    assert actual >= expected, (
        f"T3 corpus cohort has {actual} entries; expected >= {expected}. "
        "Do not truncate the corpus — add a new version instead."
    )
    assert expected >= 5, (
        f"T3 corpus expected_count={expected} is too small; must be >= 5 "
        "to provide meaningful regression coverage."
    )


def test_t3_corpus_verdicts_not_flipped():
    """Re-run simulate_tape_exit for each corpus token; assert verdict unchanged.

    This is the core T3 regression guard (AC-67.2).  For every frozen entry in
    the corpus, the settler is re-run with the same config, tape, and params.
    If any code change flips a verdict (trigger, enterable, or pnl), this test
    fails CI.

    NOTE: uses simulate_tape_exit directly (no DB) — the tape settler is pure
    functional code that does not touch the ORM.
    """
    from trading.tape_settler import simulate_tape_exit

    corpus = _load_t3_corpus()
    tape_data = _load_tape()
    config = _make_config_from_corpus(corpus)

    score_delay_s: float = corpus["score_delay_s"]
    size_sol: float = corpus["size_sol"]
    sol_usd: float = corpus["sol_usd"]

    tape_by_mint: dict = tape_data["tape"]

    failures = []
    for entry in corpus["cohort"]:
        mint: str = entry["mint"]
        expected: dict = entry["verdict"]

        assert mint in tape_by_mint, (
            f"Corpus mint {mint!r} not found in tape fixture "
            f"({_TAPE_FIXTURE.name}). "
            "Either the tape fixture was modified or the corpus mint is wrong."
        )

        trades = tape_by_mint[mint]
        if not trades:
            failures.append(f"{mint}: tape is empty")
            continue

        t0 = trades[0][0]
        entry_ts = t0 + score_delay_s

        result = simulate_tape_exit(trades, entry_ts, config, size_sol, sol_usd)

        # enterable verdict
        if result["enterable"] != expected["enterable"]:
            failures.append(
                f"{mint}: enterable FLIPPED "
                f"(was {expected['enterable']!r}, now {result['enterable']!r})"
            )
            continue

        if not result["enterable"]:
            continue  # un-enterable: no further fields to check

        # trigger verdict
        if result["trigger"] != expected["trigger"]:
            failures.append(
                f"{mint}: trigger FLIPPED "
                f"(was {expected['trigger']!r}, now {result['trigger']!r})"
            )

        # pnl (exact — any drift is a settler regression)
        if result["pnl"] != expected["pnl"]:
            failures.append(
                f"{mint}: pnl CHANGED "
                f"(was {expected['pnl']}, now {result['pnl']}, "
                f"delta={result['pnl'] - expected['pnl']})"
            )

    assert not failures, (
        f"T3 corpus verdict flip(s) detected — {len(failures)} token(s) changed:\n"
        + "\n".join(f"  • {f}" for f in failures)
        + "\n\nUpdate the corpus fixture if the change is intentional, "
        "and commit the new frozen values with the code change."
    )
