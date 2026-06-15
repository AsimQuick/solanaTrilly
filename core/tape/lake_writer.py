# ---
# module: core.tape.lake_writer
# sprint: sprint-5
# story: US-19 AC-19.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.normalized_swap, core.encoders, gzip, pathlib, datetime, typing
# ---
"""LakeWriter — append-only, daily-partitioned jsonl.gz lake writer (PRD §6.4.1).

Writes NormalizedSwaps to an immutable, append-only lake partitioned by the UTC
date derived from each batch's first swap block_time.

Partition layout:
    {base_dir}/dt=YYYY-MM-DD/part-0.jsonl.gz

Rules (PRD §6.4.1):
    - The lake is raw / immutable truth — files are NEVER mutated after the
      initial write, NEVER re-pulled.
    - Appending to an existing part file is allowed (gzip "ab" mode) so that
      multiple write() calls within the same session accumulate in one file.
    - Date is derived exclusively from block_time (UTC) — NEVER from datetime.now()
      or time.time().  This ensures the same source tape always lands in the
      same partition regardless of when the process runs.
"""
import gzip
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core.normalized_swap import NormalizedSwap


class LakeWriter:
    """Writes NormalizedSwaps to an append-only, daily-partitioned jsonl.gz lake.

    Args:
        base_dir: Root of the lake tree.  Defaults to ``lake/tapes``.
                  Tests pass ``tmp_path`` here to keep the filesystem clean.
    """

    def __init__(self, base_dir: str | Path = "lake/tapes") -> None:
        self._base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(
        self,
        swaps: list[NormalizedSwap],
        *,
        date_str: Optional[str] = None,
    ) -> Optional[Path]:
        """Append *swaps* to the correct daily part file.

        Args:
            swaps:    List of NormalizedSwap instances to persist.
            date_str: UTC date override (``"YYYY-MM-DD"``).  When provided the
                      partition date is forced to this value instead of being
                      derived from the first swap's block_time.  Intended for
                      tests only.

        Returns:
            The ``Path`` of the part file written, or ``None`` if *swaps* is empty.

        The method is idempotent with respect to the part file: if
        ``part-0.jsonl.gz`` already exists the new rows are appended (gzip "ab"
        mode).  This means multiple write() calls within one session accumulate
        in a single file under the same partition.
        """
        if not swaps:
            return None

        partition_date = date_str if date_str is not None else self._date_from_swap(swaps[0])
        part_path = self._part_path(partition_date)
        part_path.parent.mkdir(parents=True, exist_ok=True)

        # gzip "ab" → append if the file already exists; create otherwise.
        with gzip.open(part_path, "ab") as gz:
            for swap in swaps:
                line = (swap.to_json() + "\n").encode("utf-8")
                gz.write(line)

        return part_path

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _date_from_swap(swap: NormalizedSwap) -> str:
        """Return ``"YYYY-MM-DD"`` (UTC) derived from *swap.block_time*.

        Uses datetime.fromtimestamp with explicit UTC timezone — never
        datetime.now() or time.time().
        """
        return datetime.fromtimestamp(swap.block_time, tz=timezone.utc).strftime("%Y-%m-%d")

    def _part_path(self, date_str: str) -> Path:
        """Return the canonical part-0 path for the given date partition."""
        return self._base_dir / f"dt={date_str}" / "part-0.jsonl.gz"
