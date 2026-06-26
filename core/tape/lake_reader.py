# ---
# module: core.tape.lake_reader
# sprint: sprint-15
# story: US-19 AC-19.3, US-96 (schema-A normalisation)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: gzip, glob, json, pathlib, logging, typing, core.firehose.shared_tape
# ---
"""LakeReader — truncated-tail-tolerant jsonl.gz lake reader (PRD §6.4.1).

Ports ``_iter_tape_rows`` from solanabilly/app/services/paper_tape_settle.py
(the recovered-161k-rows scaffolding).  Yields all complete JSON rows from the
daily-partitioned jsonl.gz lake and silently tolerates:

- A truncated gzip tail on the open part-file (EOFError / gzip.BadGzipFile /
  OSError during readline) — yields everything before the truncation point and
  skips the rest of that file.
- A partial / malformed final line (json.JSONDecodeError) — the line is
  silently skipped; the rest of the file is unaffected.

US-96: both schema-A (solanaBilly raw-reserves) and schema-B (solanatrilly
self-tape) rows are normalised via ``normalise_row`` from
``core.firehose.shared_tape`` before being yielded.  Schema-A rows that are
missing `mint` (post-grad rows) are silently skipped.  Rows that fail
normalisation are skipped and counted on ``self.bad_lines``.

Partition layout (§6.4.1):
    {base_dir}/dt=YYYY-MM-DD/part-*.jsonl.gz
"""
import glob
import gzip
import json
import logging
from pathlib import Path
from typing import Iterator, Optional

_logger = logging.getLogger(__name__)


class LakeReader:
    """Reads NormalizedSwap dicts from the daily-partitioned jsonl.gz lake.

    Tolerates truncated tails — the open part-file may end mid-stream (the
    recorder appends crash-safe).  Every complete JSON row before the
    truncation point is yielded; the truncated fragment and the rest of that
    file are silently skipped.

    US-96: automatically normalises schema-A (solanaBilly raw-reserves) rows
    to the same format as schema-B (solanatrilly self-tape) rows via
    ``core.firehose.shared_tape.normalise_row`` + ``norm_row_to_swap_dict``.
    Rows that cannot be normalised (missing mint, zero denominators) are
    silently skipped; the count is tracked on ``self.bad_lines``.

    Args:
        base_dir: Root of the lake tree.  Defaults to ``lake/tapes``.
                  Tests pass ``tmp_path`` here to keep the filesystem clean.
        normalise: If True (default), run the schema-A/B normaliser on every
                   row.  Set False only in tests that need the raw dict.
    """

    def __init__(self, base_dir: str | Path = "lake/tapes", *, normalise: bool = False) -> None:
        self._base_dir = Path(base_dir)
        self._normalise = normalise
        self.bad_lines: int = 0   # rows skipped due to normalisation failure

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def iter_rows(self, *, date_str: Optional[str] = None) -> Iterator[dict]:
        """Yield decoded JSON dicts from part-*.jsonl.gz files in the lake.

        Args:
            date_str: When given (``"YYYY-MM-DD"``), read only the single
                      ``dt=YYYY-MM-DD/`` partition.  When ``None``, all
                      available partitions are read in ascending date order.

        Yields:
            One ``dict`` per complete JSON line decoded from the lake.

        Tolerances:
            - Cannot open a part file: logged as WARNING; file is skipped.
            - EOFError / OSError / gzip.BadGzipFile during readline: logged
              as WARNING; remaining bytes of that file are skipped; iteration
              continues with the next file.
            - json.JSONDecodeError on a line: the partial line is silently
              skipped (no log — partial trailing lines are expected on the
              open part).
        """
        if date_str is not None:
            days = [date_str]
        else:
            days = self._discover_dates()

        yield from self._iter_tape_rows(days)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _discover_dates(self) -> list[str]:
        """Return sorted list of date strings found under base_dir (dt=YYYY-MM-DD/)."""
        pattern = str(self._base_dir / "dt=*")
        dirs = glob.glob(pattern)
        dates: list[str] = []
        for d in dirs:
            name = Path(d).name  # e.g. "dt=2024-01-15"
            if name.startswith("dt="):
                dates.append(name[3:])  # strip "dt=" prefix
        return sorted(dates)

    def _iter_tape_rows(self, days: list[str]) -> Iterator[dict]:
        """Yield decoded JSON rows from daily part-*.jsonl.gz files.

        This is a direct port of ``_iter_tape_rows`` from solanabilly
        (paper_tape_settle.py lines 266-299).  Tolerates a truncated tail on
        the open part-file: the recorder appends crash-safe, so the most
        recent part may end mid-stream (EOFError from gzip).  Every complete
        line read before the truncation point is yielded; the rest of that
        file is skipped rather than aborting the whole read.

        US-96: if normalise=True (default), each raw dict passes through
        the schema-A/B normaliser; rows that fail normalisation (missing mint,
        bad reserves) are silently skipped (bad_lines counter incremented).
        The normalised row is converted back to a swap-dict for caller compat.
        """
        # Lazy import to avoid circular deps at module load time.
        if self._normalise:
            from core.firehose.shared_tape import norm_row_to_swap_dict, normalise_row

        for day in days:
            pattern = str(self._base_dir / f"dt={day}" / "part-*.jsonl.gz")
            for path in sorted(glob.glob(pattern)):
                try:
                    fh = gzip.open(path, "rt", encoding="utf-8")
                except (OSError, gzip.BadGzipFile) as exc:
                    _logger.warning("[LAKE_READER] cannot open part %s: %s", path, exc)
                    continue
                try:
                    while True:
                        try:
                            line = fh.readline()
                        except (EOFError, OSError, gzip.BadGzipFile) as exc:
                            _logger.warning("[LAKE_READER] truncated part %s: %s", path, exc)
                            break
                        if not line:
                            break
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            raw = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # partial trailing line — silently skip
                        if self._normalise:
                            norm = normalise_row(raw)
                            if norm is None:
                                self.bad_lines += 1
                                continue  # skip missing-mint / invalid rows
                            yield norm_row_to_swap_dict(norm)
                        else:
                            yield raw
                finally:
                    fh.close()
