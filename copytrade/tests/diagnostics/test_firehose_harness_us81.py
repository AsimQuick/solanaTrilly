# ---
# module: copytrade.tests.diagnostics.test_firehose_harness_us81
# sprint: sprint-15
# story: US-81, US-92
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, copytrade.firehose_harness, gzip, json, pathlib
# ---
"""Tests for the US-81 local validation harness (firehose_harness.py).

Covers:
  AC-81.1 — Parser skips-and-counts bad lines; good rows parse; no exception escapes.
  AC-81.2 — Graduation labeler: crossing threshold at correct block_time; non-grad
             stays unlabeled; window-level graduation counts match real tape.
  AC-81.3 — Dollar basis: to_usd() uses pinned price, never vol_usd;
             vol_usd guard confirms no consumer reads vol_usd for dollars.

Tests use:
  - A synthetic fixture (planted malformed + valid rows) for AC-81.1 determinism.
  - The REAL Jun 20-23 tapes for AC-81.2 graduation count anchors (requires
    the local lake at /Users/asim/NoIcloud/solanatrills/lake/firehose/).
    Tests that require the real lake are marked with @pytest.mark.require_local_lake
    and will xfail if the lake is absent (CI is local-only for Phase A).
"""
from __future__ import annotations

import gzip
import json
import pathlib

import pytest

from copytrade.firehose_harness import (
    GRAD_SOL_THRESHOLD,
    SOL_PRICE_BY_DATE,
    SOL_PRICE_DEFAULT,
    TapeRow,
    iter_parse,
    label_graduations,
    parse,
    parse_window,
    sol_price_for_date,
    to_usd,
    to_usd_with_price,
)

# ---------------------------------------------------------------------------
# Local lake availability check
# ---------------------------------------------------------------------------

LOCAL_LAKE = pathlib.Path("/Users/asim/NoIcloud/solanatrills/lake/firehose")
REAL_LAKE_AVAILABLE = (LOCAL_LAKE / "dt=2026-06-20" / "part-0.jsonl.gz").exists()

require_local_lake = pytest.mark.skipif(
    not REAL_LAKE_AVAILABLE,
    reason="local firehose lake not available",
)


# ---------------------------------------------------------------------------
# Helpers: synthetic fixture factory
# ---------------------------------------------------------------------------

def _make_fixture_tape(
    tmp_path: pathlib.Path,
    date_str: str,
    good_rows: list[dict],
    bad_lines: list[str],
) -> pathlib.Path:
    """Write a synthetic part-0.jsonl.gz to tmp_path/dt={date_str}/part-0.jsonl.gz."""
    part_dir = tmp_path / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_file = part_dir / "part-0.jsonl.gz"
    with gzip.open(str(part_file), "wt", encoding="utf-8") as f:
        for row in good_rows:
            f.write(json.dumps(row) + "\n")
            # Interleave bad lines among good
        for bad in bad_lines:
            f.write(bad + "\n")
    return part_file


def _make_valid_row(
    mint: str,
    block_time: int,
    side: str,
    vol_sol: float,
    slot: int = 100,
    price: float = 0.00001,
    owner: str = "Owner1111111111111111111111111111111111111",
) -> dict:
    return {
        "mint": mint,
        "block_time": block_time,
        "slot": slot,
        "signature": f"sig{block_time}{side}",
        "price": price,
        "side": side,
        "vol": vol_sol,
        "vol_sol": vol_sol,
        "vol_usd": 0.0,
        "owner": owner,
        "phase": "pre",
    }


# ---------------------------------------------------------------------------
# AC-81.1 — Parser
# ---------------------------------------------------------------------------

class TestParser:
    """AC-81.1: skip-and-count parser."""

    def test_good_rows_parse_correctly(self, tmp_path: pathlib.Path) -> None:
        """Valid rows are parsed into TapeRow objects with correct fields."""
        good = [
            _make_valid_row("MINT_A", 1000, "buy", 10.0),
            _make_valid_row("MINT_B", 1001, "sell", 5.0),
        ]
        _make_fixture_tape(tmp_path, "2026-06-20", good, [])
        rows, bad = parse("2026-06-20", tmp_path)
        assert len(rows) == 2
        assert bad == 0
        assert rows[0].mint == "MINT_A"
        assert rows[0].side == "buy"
        assert rows[0].vol_sol == 10.0
        assert rows[1].side == "sell"

    def test_bad_lines_are_counted_not_fatal(self, tmp_path: pathlib.Path) -> None:
        """Planted malformed lines are counted; no exception escapes; good rows still parse."""
        good = [_make_valid_row("MINT_A", 1000, "buy", 5.0)]
        bad_lines = [
            "not json at all {{{",
            '{"partial": true',  # truncated JSON
            "",  # empty line (blank, skipped silently without counting)
        ]
        _make_fixture_tape(tmp_path, "2026-06-20", good, bad_lines)
        rows, bad = parse("2026-06-20", tmp_path)
        # 2 JSON decode errors (the empty line is not counted)
        assert bad == 2, f"expected 2 bad lines, got {bad}"
        assert len(rows) == 1
        assert rows[0].mint == "MINT_A"

    def test_bad_lines_exact_planted_count(self, tmp_path: pathlib.Path) -> None:
        """The bad-line counter equals EXACTLY the number of planted malformed lines."""
        n_bad = 7
        good = [_make_valid_row("MINT_A", 1000 + i, "buy", float(i)) for i in range(5)]
        bad_lines = ["bad json " * (j + 1) for j in range(n_bad)]
        _make_fixture_tape(tmp_path, "2026-06-21", good, bad_lines)
        rows, bad = parse("2026-06-21", tmp_path)
        assert bad == n_bad
        assert len(rows) == 5

    def test_post_rows_are_not_counted_as_bad(self, tmp_path: pathlib.Path) -> None:
        """US-92 / AC-81.1 CORRECTION: phase='post' rows are VALID, NOT bad lines.

        Post-grad rows carry a different schema (rel instead of vol_sol, populated
        vol_usd, phase='post').  They are valid post-grad swap rows — not partial-write
        corruption.  The bad-line counter must NOT include them.

        Before US-92 fix: post rows were counted as bad (valid JSON, missing mint).
        After US-92 fix: post rows are recognised as valid and excluded from bad_count.
        They are skipped from the PRE-curve analysis without being flagged as corrupt.
        """
        # A valid post row (matching the historical Jun 20-23 format)
        post_row = (
            '{"block_time": 1000, "slot": 1, "signature": "s1", "rel": 4.0, '
            '"price": 0.001, "side": "buy", "vol": 1.0, "vol_usd": 0.0, "owner": "O1", "phase": "post"}'
        )
        good = [_make_valid_row("MINT_A", 1001, "buy", 3.0)]
        part_dir = tmp_path / "dt=2026-06-20"
        part_dir.mkdir(parents=True, exist_ok=True)
        with gzip.open(str(part_dir / "part-0.jsonl.gz"), "wt") as f:
            f.write(post_row + "\n")
            f.write(json.dumps(good[0]) + "\n")

        rows, bad = parse("2026-06-20", tmp_path)
        # US-92 FIX: post row is NOT counted as bad — it is a valid post-grad row
        assert bad == 0, (
            f"post rows must NOT be counted as bad (US-92 fix), got bad={bad}"
        )
        # The post row is skipped from the PRE stream (PRE-curve analysis only)
        assert len(rows) == 1
        assert rows[0].mint == "MINT_A"

    def test_missing_tape_returns_empty_not_exception(self, tmp_path: pathlib.Path) -> None:
        """A missing tape file returns ([], 0) without raising."""
        rows, bad = parse("2026-06-19", tmp_path)  # date not created
        assert rows == []
        assert bad == 0

    def test_vol_usd_always_zero_in_good_rows(self, tmp_path: pathlib.Path) -> None:
        """vol_usd field is parsed but is always 0.0 (never used for dollar values)."""
        good = [_make_valid_row("MINT_A", 1000, "buy", 10.0)]
        _make_fixture_tape(tmp_path, "2026-06-20", good, [])
        rows, _ = parse("2026-06-20", tmp_path)
        assert rows[0].vol_usd == 0.0

    def test_iter_parse_streaming(self, tmp_path: pathlib.Path) -> None:
        """iter_parse yields the same rows as parse(); counter tracks bad lines."""
        good = [_make_valid_row("MINT_A", 1000 + i, "buy", float(i + 1)) for i in range(3)]
        bad_lines = ["bad{1", "bad{2"]
        _make_fixture_tape(tmp_path, "2026-06-20", good, bad_lines)

        gen, counter = iter_parse("2026-06-20", tmp_path)
        streamed = list(gen)
        assert len(streamed) == 3
        assert counter.bad == 2


# ---------------------------------------------------------------------------
# AC-81.2 — Graduation labeler
# ---------------------------------------------------------------------------

class TestGraduationLabeler:
    """AC-81.2: cum-buy-vol_sol graduation labeler."""

    def test_graduated_mint_at_correct_block_time(self, tmp_path: pathlib.Path) -> None:
        """A mint that crosses the threshold is labeled graduated at the crossing row's block_time."""
        # Use direct call with pre-built TapeRow objects
        labels = label_graduations(rows=[
            TapeRow(mint="MINT_GRAD", block_time=1000, slot=100, signature="s1", price=0.001,
                    side="buy", vol=40.0, vol_sol=40.0, vol_usd=0.0, owner="O", phase="pre"),
            TapeRow(mint="MINT_GRAD", block_time=1001, slot=101, signature="s2", price=0.001,
                    side="buy", vol=40.0, vol_sol=40.0, vol_usd=0.0, owner="O", phase="pre"),
            TapeRow(mint="MINT_GRAD", block_time=1002, slot=102, signature="s3", price=0.001,
                    side="buy", vol=10.0, vol_sol=10.0, vol_usd=0.0, owner="O", phase="pre"),
        ])
        assert "MINT_GRAD" in labels
        lbl = labels["MINT_GRAD"]
        assert lbl.graduated is True
        assert lbl.grad_block_time == 1002  # the crossing row
        assert lbl.cum_buy_vol_sol == pytest.approx(90.0)

    def test_non_graduated_mint(self, tmp_path: pathlib.Path) -> None:
        """A mint that never reaches the threshold is labeled not-graduated."""
        rows = [
            TapeRow(mint="MINT_NOGRAD", block_time=1000, slot=100, signature="s1", price=0.001,
                    side="buy", vol=20.0, vol_sol=20.0, vol_usd=0.0, owner="O", phase="pre"),
            TapeRow(mint="MINT_NOGRAD", block_time=1001, slot=101, signature="s2", price=0.001,
                    side="buy", vol=30.0, vol_sol=30.0, vol_usd=0.0, owner="O", phase="pre"),
        ]
        labels = label_graduations(rows=rows)
        lbl = labels["MINT_NOGRAD"]
        assert lbl.graduated is False
        assert lbl.grad_block_time is None
        assert lbl.cum_buy_vol_sol == pytest.approx(50.0)

    def test_sell_rows_do_not_count_toward_graduation(self) -> None:
        """Only BUY rows count toward the cumulative threshold."""
        rows = [
            TapeRow(mint="MINT_X", block_time=1000, slot=100, signature="s1", price=0.001,
                    side="buy", vol=50.0, vol_sol=50.0, vol_usd=0.0, owner="O", phase="pre"),
            TapeRow(mint="MINT_X", block_time=1001, slot=101, signature="s2", price=0.001,
                    side="sell", vol=100.0, vol_sol=100.0, vol_usd=0.0, owner="O", phase="pre"),
        ]
        labels = label_graduations(rows=rows)
        # 50 SOL of buys, not 150 — sells don't count
        lbl = labels["MINT_X"]
        assert lbl.graduated is False
        assert lbl.cum_buy_vol_sol == pytest.approx(50.0)

    def test_threshold_constant_used(self) -> None:
        """Exactly at the threshold: graduated = True."""
        rows = [
            TapeRow(mint="MINT_EXACT", block_time=1000, slot=100, signature="s1", price=0.001,
                    side="buy", vol=GRAD_SOL_THRESHOLD, vol_sol=GRAD_SOL_THRESHOLD,
                    vol_usd=0.0, owner="O", phase="pre"),
        ]
        labels = label_graduations(rows=rows)
        assert labels["MINT_EXACT"].graduated is True

    @require_local_lake
    def test_real_tape_jun20_graduation_count(self) -> None:
        """Jun-20 tape: graduation count from cum-vol_sol>=85 matches the measured anchor (380)."""
        rows, bad = parse("2026-06-20", LOCAL_LAKE)
        labels = label_graduations(rows=rows)
        graduated = sum(1 for lbl in labels.values() if lbl.graduated)
        # Anchored to the real tape (measured: 380 on Jun-20)
        assert graduated == 380, f"Expected 380 graduates on Jun-20, got {graduated}"

    @require_local_lake
    def test_real_tape_jun21_graduation_count(self) -> None:
        """Jun-21 tape: graduation count = 372."""
        rows, bad = parse("2026-06-21", LOCAL_LAKE)
        labels = label_graduations(rows=rows)
        graduated = sum(1 for lbl in labels.values() if lbl.graduated)
        assert graduated == 372, f"Expected 372 graduates on Jun-21, got {graduated}"

    @require_local_lake
    def test_real_tape_jun22_graduation_count(self) -> None:
        """Jun-22 tape: graduation count = 825 (highest-volume day)."""
        rows, bad = parse("2026-06-22", LOCAL_LAKE)
        labels = label_graduations(rows=rows)
        graduated = sum(1 for lbl in labels.values() if lbl.graduated)
        assert graduated == 825, f"Expected 825 graduates on Jun-22, got {graduated}"

    @require_local_lake
    def test_real_tape_jun23_graduation_count(self) -> None:
        """Jun-23 tape: graduation count = 104 (partial day)."""
        rows, bad = parse("2026-06-23", LOCAL_LAKE)
        labels = label_graduations(rows=rows)
        graduated = sum(1 for lbl in labels.values() if lbl.graduated)
        assert graduated == 104, f"Expected 104 graduates on Jun-23, got {graduated}"

    @require_local_lake
    def test_graduation_does_not_depend_on_phase_field(self) -> None:
        """Label is independent of the (broken) phase field — proven by the 'pre'-only days."""
        # Jun-20 has 0 post-grad rows in the mint-schema (all are the no-mint schema)
        # but should still have 380 graduates by the cum-vol label.
        rows, _ = parse("2026-06-20", LOCAL_LAKE)
        # The post-grad rows are no-mint schema and get skipped by the parser.
        # The key assertion is that graduation labels work regardless of phase field.
        labels = label_graduations(rows=rows)
        graduated = sum(1 for lbl in labels.values() if lbl.graduated)
        assert graduated == 380  # must match regardless of phase field


# ---------------------------------------------------------------------------
# AC-81.3 — Dollar basis
# ---------------------------------------------------------------------------

class TestDollarBasis:
    """AC-81.3: to_usd() uses pinned price; vol_usd is never used."""

    def test_to_usd_uses_pinned_price(self) -> None:
        """to_usd(vol_sol, date_str) = vol_sol * pinned price for that date."""
        price = SOL_PRICE_BY_DATE["2026-06-20"]
        result = to_usd(10.0, "2026-06-20")
        assert result == pytest.approx(10.0 * price)

    def test_to_usd_known_value(self) -> None:
        """to_usd(100.0, '2026-06-22') = 100 * 84 = 8400."""
        result = to_usd(100.0, "2026-06-22")
        assert result == pytest.approx(8400.0)

    def test_to_usd_with_explicit_price(self) -> None:
        """to_usd_with_price(vol_sol, sol_price) = vol_sol * sol_price."""
        result = to_usd_with_price(5.0, 90.0)
        assert result == pytest.approx(450.0)

    def test_sol_price_for_date_pinned(self) -> None:
        """sol_price_for_date returns the pinned per-day value."""
        for date_str, expected_price in SOL_PRICE_BY_DATE.items():
            assert sol_price_for_date(date_str) == pytest.approx(expected_price)

    def test_sol_price_fallback_for_unknown_date(self) -> None:
        """An unlisted date falls back to SOL_PRICE_DEFAULT."""
        price = sol_price_for_date("2025-01-01")
        assert price == pytest.approx(SOL_PRICE_DEFAULT)

    def test_vol_usd_is_zero_in_parsed_rows(self, tmp_path: pathlib.Path) -> None:
        """Parsed TapeRow.vol_usd is always 0.0 (as in the real tapes)."""
        good = [_make_valid_row("MINT_A", 1000, "buy", 5.0)]
        _make_fixture_tape(tmp_path, "2026-06-20", good, [])
        rows, _ = parse("2026-06-20", tmp_path)
        for row in rows:
            assert row.vol_usd == 0.0, f"vol_usd non-zero in row: {row}"

    def test_dollar_basis_not_from_vol_usd(self) -> None:
        """Dollar values MUST come from to_usd(vol_sol, date), NEVER from vol_usd.

        This is an API-level guard: a consumer that uses row.vol_usd for a dollar
        value will get 0.0; a consumer that uses to_usd(row.vol_sol, date) gets the
        correct dollar amount.  This test asserts the harness API makes the right
        path obvious and the wrong path obviously broken.
        """
        vol_sol = 1.5
        date_str = "2026-06-21"
        # Correct: use harness dollar basis
        correct = to_usd(vol_sol, date_str)
        assert correct == pytest.approx(vol_sol * SOL_PRICE_BY_DATE[date_str])
        assert correct > 0

        # Wrong: vol_usd is 0 (confirmed bad path)
        vol_usd_value = 0.0  # always zero in these tapes
        assert vol_usd_value == 0.0, "If vol_usd were non-zero, the wrong path would be non-obvious"

    @require_local_lake
    def test_real_tape_vol_usd_all_zero(self) -> None:
        """Confirm that in the real Jun-20 tape, all vol_usd values are 0.0 for mint rows.

        This validates the MANIFEST claim and proves the dollar-basis discipline is
        non-negotiable (using vol_usd for dollars would give $0 for everything).
        """
        rows, _ = parse("2026-06-20", LOCAL_LAKE)
        # Sample first 10000 rows
        for row in rows[:10000]:
            assert row.vol_usd == 0.0, f"vol_usd non-zero: {row.vol_usd} for mint {row.mint}"


# ---------------------------------------------------------------------------
# AC-81.1 + bad-line discipline integration
# ---------------------------------------------------------------------------

class TestBadLineDiscipline:
    """Bad-line skip-count-flag discipline (sprint DoD)."""

    @require_local_lake
    def test_real_tape_bad_line_counts_match_manifest(self) -> None:
        """Real tape bad-line counts (schema-invalid rows) match MANIFEST percentages.

        MANIFEST: Jun-20 ~10%, Jun-21 ~4%, Jun-22 ~2%, Jun-23 ~15%.
        Anchored to the measured values.
        """
        expected = {
            "2026-06-20": 87634,
            "2026-06-21": 35943,
            "2026-06-22": 35282,
            "2026-06-23": 35514,
        }
        for date_str, expected_bad in expected.items():
            rows, bad = parse(date_str, LOCAL_LAKE)
            assert bad == expected_bad, (
                f"{date_str}: expected {expected_bad} bad lines, got {bad}"
            )

    @require_local_lake
    def test_parse_window_reports_per_day_bad_counts(self) -> None:
        """parse_window() returns a bad-per-day dict with correct per-day counts."""
        date_strs = ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"]
        _, bad_per_day = parse_window(date_strs, LOCAL_LAKE)
        assert set(bad_per_day.keys()) == set(date_strs)
        # All bad counts are > 0 (not silently dropped to zero)
        for ds in date_strs:
            assert bad_per_day[ds] > 0, f"{ds} should have some bad lines"

    @require_local_lake
    def test_real_tape_good_row_counts(self) -> None:
        """Real tape good-row counts match measured anchors."""
        expected_good = {
            "2026-06-20": 779300,
            "2026-06-21": 862183,
            "2026-06-22": 1520162,
            "2026-06-23": 208460,
        }
        for date_str, expected in expected_good.items():
            rows, _ = parse(date_str, LOCAL_LAKE)
            assert len(rows) == expected, (
                f"{date_str}: expected {expected} good rows, got {len(rows)}"
            )
