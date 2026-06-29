# ---
# module: core.backfill.lake_backfill
# sprint: epic-tape-sourcing-escalation
# story: EPIC-tape-sourcing-escalation Tier 2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: core.tape.lake_reader, core.firehose.tape_sink, datetime, pathlib, logging
# ---
"""LakeBackfiller — Tier 2 tape-sourcing escalation (local lake, free).

When a graduated token is due to score but the in-memory TapeStore buffer
is empty (the daemon restarted, or the pre-grad collection window was missed),
this worker scans the firehose lake for that mint's pre-graduation swaps and
returns them so the daemon can populate the buffer via
``TapeStore.load_without_sink`` — no Birdeye REST credit consumed, no
duplication risk.

Design
------
- Scans partitions from ``grad_date - 3d`` through ``grad_date`` (bonding
  curves cap at ~3 days; usually 1-2 date partitions).
- Filters rows: ``mint == mint AND block_time < graduated_block_time AND
  phase == "pre"``.  The ``phase`` filter is CRITICAL — post-grad rows from
  the same mint have positive ``rel`` values and silently produce bad features
  if fed to the feature assembler.
- Uses ``LakeReader(base_dir=FIREHOSE_LAKE_BASE)`` — the firehose lake, NOT
  the default ``lake/tapes``.  The constant is imported from tape_sink so
  there is a single source of truth and no bare string literals here.
- Principle #7: no ``datetime.now()`` / ``time.time()`` in this module; all
  date math is derived from ``graduated_block_time`` (caller-supplied).
- Testable in isolation as a pure sync function (no Django ORM, no asyncio).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lake-path constant — imported from tape_sink (single source of truth).
# NEVER use a bare "lake/firehose" string literal here; if the path in
# tape_sink ever changes, this import ensures the backfiller follows.
# ---------------------------------------------------------------------------
# Imported below inside the class to avoid a top-level circular-import risk
# in environments where tape_sink is imported very early.  The constant is
# accessed on first call, not at module parse time.


def _firehose_lake_base() -> Path:
    """Return the FIREHOSE_LAKE_BASE constant from core.firehose.tape_sink."""
    from core.firehose.tape_sink import FIREHOSE_LAKE_BASE  # noqa: PLC0415

    return FIREHOSE_LAKE_BASE


class LakeBackfiller:
    """Synchronous worker that reads a mint's pre-grad swaps from the lake.

    Designed to be called via ``asgiref.sync.sync_to_async`` from an asyncio
    context so it never blocks the event loop in the daemon.

    Args:
        base_dir: Override for the lake root (default: ``FIREHOSE_LAKE_BASE``
                  from ``core.firehose.tape_sink``).  Tests pass ``tmp_path``
                  here to keep the real lake clean.
    """

    def __init__(
        self, base_dir: Path | str | None = None, *, normalise: bool = False
    ) -> None:
        if base_dir is not None:
            self._base_dir = Path(base_dir)
        else:
            self._base_dir = _firehose_lake_base()
        # normalise=True is REQUIRED when scanning solanaBilly's shared tape
        # (lake/billy_tape, schema-A raw-reserves): the LakeReader runs
        # normalise_row so schema-A rows become the same swap-dict shape (with
        # 'price', 'phase'="pre") the in-memory TapeStore holds.  Without it,
        # schema-A rows lack 'price' and the pf/v7 feature builders see 0 swaps.
        self._normalise = normalise

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_for_mint(
        self,
        mint: str,
        graduated_block_time: int,
    ) -> list[dict]:
        """Scan the lake for *mint*'s pre-grad swaps and return them.

        Scans partitions from ``grad_date - 3 days`` through ``grad_date``
        (inclusive).  Filters rows where:
        - ``row["mint"] == mint``  (cross-mint exclusion)
        - ``row["block_time"] < graduated_block_time``  (pre-grad only)
        - ``row["phase"] == "pre"``  (CRITICAL: exclude post-grad rows from
          the same mint; post-grad rows have positive rel and corrupt features)

        Args:
            mint:                   The Solana mint address.
            graduated_block_time:   Unix epoch seconds of the graduation block.
                                    All date arithmetic is derived from this
                                    value (no wall-clock calls — Principle #7).

        Returns:
            List of swap dicts (same shape as the live TapeStore buffer holds).
            Empty list if the lake has no matching rows or the partition files
            do not exist yet.
        """
        from core.tape.lake_reader import LakeReader  # noqa: PLC0415

        # Derive the date range from graduated_block_time (no datetime.now()).
        # Bonding curves are capped at ~3 days so scanning [grad-3d, grad] is
        # sufficient; it is typically just 1-2 partitions.
        grad_dt = datetime.fromtimestamp(graduated_block_time, tz=timezone.utc)
        date_strs = self._partition_dates(grad_dt, lookback_days=3)

        reader = LakeReader(base_dir=self._base_dir, normalise=self._normalise)

        results: list[dict] = []
        rows_scanned = 0
        for date_str in date_strs:
            for row in reader.iter_rows(date_str=date_str):
                rows_scanned += 1
                # Cross-mint guard: the partition may contain many mints.
                if row.get("mint") != mint:
                    continue
                # Pre-grad guard: rows up to AND INCLUDING the graduation block
                # (bt <= grad) to match training (build_universe uses
                # timestamp <= gts).  Instant/single-block graduations put the
                # whole bonding curve in the grad block; a strict bt<grad dropped
                # them.  Same-block POST-grad AMM rows are still excluded by the
                # phase guard below (phase=="post"), so this admits only curve rows.
                try:
                    bt = int(row["block_time"])
                except (KeyError, TypeError, ValueError):
                    continue
                if bt > graduated_block_time:
                    continue
                # Phase guard: post-grad rows written by the sink carry
                # phase="post"; feeding them would corrupt the feature vector.
                if row.get("phase") != "pre":
                    continue
                results.append(row)

        logger.info(
            "[LAKE_BACKFILL] mint=%s grad_bt=%d partitions=%s "
            "rows_scanned=%d matched=%d",
            mint,
            graduated_block_time,
            date_strs,
            rows_scanned,
            len(results),
        )
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _partition_dates(grad_dt: datetime, *, lookback_days: int = 3) -> list[str]:
        """Return date strings ``["YYYY-MM-DD", ...]`` from grad-N days to grad.

        The list is ordered oldest-first so the reader scans chronologically
        (matches the order the sink writes, which makes early-exit possible in
        future optimizations).

        Args:
            grad_dt:      The graduation datetime (UTC).
            lookback_days: How many calendar days before grad to include.

        Returns:
            List of ``"YYYY-MM-DD"`` strings, e.g. for grad_dt=2024-03-15
            and lookback_days=3: ``["2024-03-12", "2024-03-13",
            "2024-03-14", "2024-03-15"]``.
        """
        dates: list[str] = []
        for offset in range(lookback_days, -1, -1):
            day = (grad_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
            dates.append(day)
        return dates
