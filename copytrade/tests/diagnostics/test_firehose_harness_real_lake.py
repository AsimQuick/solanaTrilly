# ---
# module: copytrade.tests.diagnostics.test_firehose_harness_real_lake
# sprint: sprint-15
# story: US-81
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: pytest, copytrade.firehose_harness
# ---
"""Real-lake validation tests for the US-81 harness.

These tests run against the ACTUAL Jun 20-23 firehose tapes and assert
PINNED DETERMINISTIC COUNTS (parse counts, graduation counts, bad-line counts).
They are the "local proof" that the AC-81 harness is correct against real data.

REQUIREMENTS:
  - Must be run with the local lake mounted at /app/ext_lake inside Docker, OR
    the lake available at the default path on the host.
  - In Docker with lake mounted:
      docker compose run --rm -v /Users/asim/NoIcloud/solanatrills/lake/firehose:/app/ext_lake web \
        python -m pytest copytrade/tests/diagnostics/test_firehose_harness_real_lake.py

  - The test configures the lake path at /app/ext_lake (Docker mount) with
    fallback to the macOS local path for native runs.

PINNED ANCHORS (measured 2026-06-23 against byte-exact copies):
  Date       | Good rows  | Bad rows | Grads (cum_buy>=85)
  2026-06-20 | 779,300    | 87,634   | 380
  2026-06-21 | 862,183    | 35,943   | 372
  2026-06-22 | 1,520,162  | 35,282   | 825
  2026-06-23 | 208,460    | 35,514   | 104
  Total grads: 1,681 over 4 days (~420/day avg; Jun-22 highest volume)
"""
from __future__ import annotations

import pathlib

import pytest

# Determine lake path: Docker mount first, then local macOS path
_DOCKER_LAKE = pathlib.Path("/app/ext_lake")
_LOCAL_LAKE = pathlib.Path("/Users/asim/NoIcloud/solanatrills/lake/firehose")

if _DOCKER_LAKE.exists() and (_DOCKER_LAKE / "dt=2026-06-20" / "part-0.jsonl.gz").exists():
    LAKE_BASE = _DOCKER_LAKE
elif _LOCAL_LAKE.exists() and (_LOCAL_LAKE / "dt=2026-06-20" / "part-0.jsonl.gz").exists():
    LAKE_BASE = _LOCAL_LAKE
else:
    LAKE_BASE = None

require_lake = pytest.mark.skipif(
    LAKE_BASE is None,
    reason="Real firehose lake not available (not mounted at /app/ext_lake or local path)",
)

# Pinned anchors (measured 2026-06-23 from Docker with _validate_row schema guard)
# Note: Docker counts differ slightly from a raw Python scan because _validate_row
# applies strict schema validation (side must be 'buy'|'sell', etc.) in addition
# to the missing-mint check.  These are the authoritative Docker-measured values.
EXPECTED = {
    "2026-06-20": {"good": 779300, "bad": 87634, "grads": 380},
    "2026-06-21": {"good": 861943, "bad": 36183, "grads": 372},
    "2026-06-22": {"good": 1520021, "bad": 35423, "grads": 825},
    "2026-06-23": {"good": 208460, "bad": 35514, "grads": 104},
}


@require_lake
class TestRealLakeParseAnchors:
    """Parse count anchors for each day — AC-81.1 proof."""

    @pytest.mark.parametrize("date_str", ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"])
    def test_good_row_count_pinned(self, date_str: str) -> None:
        """Good row count matches pinned anchor for each date."""
        from copytrade.firehose_harness import parse
        rows, bad = parse(date_str, LAKE_BASE)
        expected = EXPECTED[date_str]["good"]
        assert len(rows) == expected, (
            f"{date_str}: expected {expected} good rows, got {len(rows)}"
        )

    @pytest.mark.parametrize("date_str", ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"])
    def test_bad_line_count_pinned(self, date_str: str) -> None:
        """Bad-line count matches pinned anchor for each date."""
        from copytrade.firehose_harness import parse
        rows, bad = parse(date_str, LAKE_BASE)
        expected = EXPECTED[date_str]["bad"]
        assert bad == expected, (
            f"{date_str}: expected {expected} bad lines, got {bad}"
        )


@require_lake
class TestRealLakeGradAnchors:
    """Graduation label anchors for each day — AC-81.2 proof."""

    @pytest.mark.parametrize("date_str", ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"])
    def test_graduation_count_pinned(self, date_str: str) -> None:
        """Graduation count matches pinned anchor for each date."""
        from copytrade.firehose_harness import label_graduations, parse
        rows, _ = parse(date_str, LAKE_BASE)
        labels = label_graduations(rows=rows)
        grads = sum(1 for lbl in labels.values() if lbl.graduated)
        expected = EXPECTED[date_str]["grads"]
        assert grads == expected, (
            f"{date_str}: expected {expected} graduates, got {grads}"
        )

    def test_total_graduation_count_window(self) -> None:
        """Total graduation count across all 4 days = 1,681."""
        from copytrade.firehose_harness import label_graduations, parse
        total_grads = 0
        for date_str in EXPECTED:
            rows, _ = parse(date_str, LAKE_BASE)
            labels = label_graduations(rows=rows)
            total_grads += sum(1 for lbl in labels.values() if lbl.graduated)
        expected_total = sum(v["grads"] for v in EXPECTED.values())  # 380+372+825+104 = 1681
        assert total_grads == expected_total, (
            f"Expected {expected_total} total graduates (4-day window), got {total_grads}"
        )

    @pytest.mark.parametrize("date_str", ["2026-06-20", "2026-06-21", "2026-06-22", "2026-06-23"])
    def test_vol_usd_all_zero(self, date_str: str) -> None:
        """All parsed rows have vol_usd == 0.0 (broken field — confirmed zero)."""
        from copytrade.firehose_harness import parse
        rows, _ = parse(date_str, LAKE_BASE)
        # Sample first 5000 to keep it fast
        for row in rows[:5000]:
            assert row.vol_usd == 0.0, (
                f"{date_str}: found non-zero vol_usd={row.vol_usd} for {row.mint}"
            )
