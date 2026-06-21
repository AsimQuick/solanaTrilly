# ---
# module: core.firehose.tape_sink
# sprint: epic-tape-sourcing-escalation
# story: EPIC-tape-sourcing-escalation Tier 2
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: gzip, json, datetime, pathlib
# ---
"""LakeTapeSink — durable persistence for the live firehose swap tape (AC-3).

The firehose buffers swaps in-memory (``TapeStore``) and consumes them at score
time; previously they were never persisted, so the raw tape was lost when the
window ended and the ``swaps`` data-contract surface stayed empty.  This sink
appends every collected swap to the daily-partitioned jsonl.gz lake — the SAME
``lake/firehose/dt=YYYY-MM-DD/part-0.jsonl.gz`` layout the ``LakeReader`` and
the US-78 swaps export already read — so a soak banks a durable, replayable tape.

Design: append to an in-memory batch on ``record()`` (fast, non-blocking on the
collection hot path); flush to disk inline once the batch reaches ``flush_every``
(a small gzip append, a few ms).  The daemon calls ``flush()`` once more on
shutdown.  ``record()`` NEVER raises — a persistence failure must not perturb the
live collection / scoring path.

Each row is the buffered swap dict (mint, block_time, slot, signature, price,
side, vol/vol_sol/vol_usd, owner) plus a ``phase`` ("pre" | "post") so the swaps
export derives the venue (pre->pump_dot_fun, post->pump_amm).  Date partition is
derived from ``block_time`` (UTC) — never from wall-clock — so the same tape
always lands in the same partition.

Epoch-zero / bad-timestamp guard
---------------------------------
Rows whose ``block_time`` is 0, None, or predates the Solana genesis epoch
(``_MIN_VALID_BLOCK_TIME``, 2020-01-01 UTC ≈ epoch 1 577 836 800) are silently
DROPPED at flush time (logged at WARNING) rather than being written into a
``dt=1970`` or ``dt=1977`` partition.  This prevents the bad-timestamp bucketing
bug where a Birdeye row arriving with ``block_time=0`` / a slot-sized integer /
or any other pre-2020 value creates a junk date partition in the live lake.

Dropped rows are counted on ``self.quarantined`` (telemetry).
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: The root directory of the firehose lake.  This is the DEDICATED lake that
#: LakeTapeSink writes to and the Tier-2/3 backfill reads from.  It is SEPARATE
#: from ``lake/tapes`` (the dashboard's historical lake).  Exported as a module
#: constant so every consumer (tape_sink, lake_backfill, tests) uses the SAME
#: path — never a bare string literal.  A mismatch here = silent no-op (the
#: backfiller scans the wrong tree and returns 0 rows every time).
FIREHOSE_LAKE_BASE: Path = Path("lake/firehose")

#: Hard cap on the in-memory batch so a persistent disk failure can never grow
#: memory without bound — oldest rows are dropped (logged) past this.
_MAX_BUFFER = 100_000

#: Minimum plausible Solana block_time (2020-01-01T00:00:00Z).  Any row whose
#: ``block_time`` is at or before this value (or is None/0) is almost certainly
#: a bad timestamp (epoch-zero, a raw slot number, or a Birdeye parsing failure)
#: and is dropped at flush — never written into a pre-2020 dt= partition.
_MIN_VALID_BLOCK_TIME: int = 1_577_836_800  # 2020-01-01 00:00:00 UTC


class LakeTapeSink:
    """Append-only durable sink for live firehose swaps (AC-3).

    Args:
        base_dir:    Lake root.  Defaults to ``lake/firehose`` — a DEDICATED firehose
                     tape lake, SEPARATE from the dashboard's ``lake/tapes`` (which the
                     candle/cohort/token-detail APIs scan in full on every request).
                     Keeping the high-volume live torrent out of ``lake/tapes`` keeps
                     those dashboard APIs fast; the US-78 swaps export reads here.
        flush_every: Flush to disk once this many rows are buffered (default 250).
    """

    def __init__(self, base_dir: str | Path = "lake/firehose", *, flush_every: int = 250) -> None:
        self._base_dir = Path(base_dir)
        self._flush_every = max(1, int(flush_every))
        self._buf: list[dict] = []
        self.written: int = 0      # cumulative rows flushed to disk (telemetry)
        self.quarantined: int = 0  # cumulative rows dropped (bad block_time)

    # ------------------------------------------------------------------
    # Hot-path ingest — never raises
    # ------------------------------------------------------------------

    def record(self, swap: dict, phase: str) -> None:
        """Buffer one swap for durable persistence; flush inline when full.

        Resilient by contract: any error is swallowed (logged once) so the live
        collection path is never perturbed by durable persistence.
        """
        try:
            row = dict(swap)
            row["phase"] = phase
            self._buf.append(row)
            if len(self._buf) >= self._flush_every:
                self.flush()
            elif len(self._buf) > _MAX_BUFFER:
                # Disk has been failing — shed oldest to bound memory.
                drop = len(self._buf) - _MAX_BUFFER
                del self._buf[:drop]
                logger.warning("[TAPE_SINK] buffer over cap — dropped %d oldest rows.", drop)
        except Exception as exc:  # noqa: BLE001 — durable sink must never break collection
            logger.warning("[TAPE_SINK] record failed (%s) — continuing.", exc)

    # ------------------------------------------------------------------
    # Flush — group by UTC date, gzip-append
    # ------------------------------------------------------------------

    def flush(self) -> int:
        """Write the buffered batch to the daily-partitioned lake. Returns rows written."""
        if not self._buf:
            return 0
        batch, self._buf = self._buf, []
        try:
            by_date: dict[str, list[dict]] = {}
            dropped = 0
            for row in batch:
                raw_bt = row.get("block_time")
                try:
                    bt = int(raw_bt)
                except (TypeError, ValueError):
                    bt = 0
                # Guard: drop rows with implausible block_time (epoch-zero, raw
                # slot numbers, pre-2020 timestamps) — they would create junk
                # dt=1970/1977/… partitions in the lake.
                if bt <= _MIN_VALID_BLOCK_TIME:
                    dropped += 1
                    continue
                date_str = datetime.fromtimestamp(bt, tz=timezone.utc).strftime("%Y-%m-%d")
                by_date.setdefault(date_str, []).append(row)
            if dropped:
                logger.warning(
                    "[TAPE_SINK] dropped %d row(s) with bad block_time (<= %d / pre-2020).",
                    dropped,
                    _MIN_VALID_BLOCK_TIME,
                )
                self.quarantined += dropped
            good = len(batch) - dropped
            for date_str, rows in by_date.items():
                part = self._base_dir / f"dt={date_str}" / "part-0.jsonl.gz"
                part.parent.mkdir(parents=True, exist_ok=True)
                with gzip.open(part, "ab") as gz:
                    for row in rows:
                        gz.write((json.dumps(row) + "\n").encode("utf-8"))
            self.written += good
            return good
        except Exception as exc:  # noqa: BLE001
            # Re-buffer the batch so the next flush retries (bounded by _MAX_BUFFER).
            logger.warning("[TAPE_SINK] flush failed (%s) — re-buffering %d rows.", exc, len(batch))
            self._buf = batch + self._buf
            return 0
