# ---
# module: core.firehose.shared_tape
# sprint: sprint-15
# story: US-96
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: glob, gzip, json, os, time, logging, dataclasses
# ---
"""shared_tape.py — solanaBilly shared-tape reader and schema normaliser (US-96).

ARCHITECTURE
============
solanaBilly writes its Helius firehose to ``/root/tape/dt=YYYY-MM-DD/part-*.jsonl.gz``
(schema A — raw reserves).  Under ``TAPE_SOURCE=shared_billy``, solanatrilly reads
that shared tape READ-ONLY and normalises every schema-A row to the same ``NormRow``
that the scoring/settle loop already consumes.

Two schemas are handled transparently:
  A  (solanaBilly / raw reserves)   — fields: virtual_sol_reserves, virtual_token_reserves,
                                       sol_amount, token_amount, real_sol_reserves
  B  (solanatrilly self-tape)        — fields: price, vol_sol, phase

Schema-A is strictly RICHER:
  * price        = virtual_sol_reserves / virtual_token_reserves  (exact curve-state price)
  * vol_sol      = sol_amount / 1e9
  * curve_frac   = (virtual_sol_reserves/1e9 - 30) / 85          (exact from reserves)
  * depth_sol    = real_sol_reserves / 1e9                         (real book depth)
  * token_amount present (exact token flow)

FRESHNESS PRECONDITION
======================
``billy_firehose_is_live(root, max_age_s)`` checks the newest part-file mtime.

VPS-VERIFIED cadence (2026-06-26):
  * solanaBilly mtime updates per-write (~every 30s on the live part file).
  * Part files ROLL every ~45-50 min (new epoch-seq filename).
  * A freshness threshold of 600 s (10 min) is safe — far above the 30s
    per-write cadence and far below any reasonable production outage window.

BILLY_TAPE_MAX_AGE_S constant is the chosen threshold (override via env/settings).

TAILING
=======
``BillyTapeTailer`` watches ``/app/lake/billy_tape`` for new part-*.jsonl.gz
files and reads newly appended lines, feeding them into an asyncio.Queue for
the existing _scoring_loop consumer.  It does NOT write any tape.
"""
from __future__ import annotations

import asyncio
import glob
import gzip
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# ---- Constants ---------------------------------------------------------------

#: Default mount path for solanaBilly's read-only shared tape inside the container.
BILLY_TAPE_DEFAULT_ROOT: str = "/app/lake/billy_tape"

#: Freshness threshold (seconds).  The live part-file mtime on the VPS updates
#: roughly every 30 s (per-write). 600 s is 20x that cadence — comfortably safe.
BILLY_TAPE_MAX_AGE_S: int = 600

#: Schema-A curve-cap constants (pump.fun bonding curve):
#:   virtual SOL at graduation = 30 (offset) + 85 (fillable) = 115 SOL
_VIRT_OFFSET_SOL: float = 30.0
_CURVE_CAP_SOL: float = 85.0

#: The verbatim warning string emitted at enable-time or startup when stale.
STALE_WARNING: str = "You must turn on solanaBilly's firehose."


# ---- Normalised row dataclass ------------------------------------------------

@dataclass
class NormRow:
    """A normalised swap row accepted by the scoring/settle loop.

    Fields match what the existing downstream consumers (assemble_pregrad_features,
    LakeReader consumers, curvestage settler, US-78 swaps export) already expect.

    Schema-A specific extras (None on schema-B rows):
      curve_frac_exact — exact from reserves ((vsol/1e9 - 30) / 85)
      depth_sol        — real_sol_reserves / 1e9
      token_amount     — exact token flow (needed by US-93 balance-based features)
    """
    mint: str
    block_time: int
    slot: int
    signature: str
    price: float           # SOL/token (curve-state for A, executed for B)
    side: str              # "buy" | "sell"
    vol_sol: float         # absolute SOL flow
    vol_usd: float         # 0.0 for schema-A pre rows (pre-only vol_usd=0 bug)
    owner: str
    phase: str             # "pre" | "post"
    # Schema-A extras (None for schema-B)
    curve_frac_exact: Optional[float] = None
    depth_sol: Optional[float] = None
    token_amount: Optional[int] = None
    received_at: Optional[float] = None


# ---- Schema detection + normalisation ----------------------------------------

def _is_schema_a(raw: dict) -> bool:
    """Return True if *raw* is a schema-A (solanaBilly raw-reserves) row."""
    return "virtual_sol_reserves" in raw


def normalise_row(raw: dict) -> Optional[NormRow]:
    """Normalise a raw parsed JSON dict to ``NormRow``.

    Handles schema A (solanaBilly, raw reserves) and schema B (solanatrilly
    self-tape, decoded).  Returns None if the row is unparseable / invalid
    (missing required fields, zero denominators, etc.).

    Schema A → price = vsol / vtok (exact curve-state price)
    Schema B → price field used directly

    This is the SINGLE normalisation point used by:
      - the consume/scoring loop (shared_billy path)
      - LakeReader (when reading schema-A tapes)
      - the US-78 swaps export
      - the curvestage settler
    """
    mint = raw.get("mint")
    if not mint:
        return None  # post-grad row (no mint) or corrupt

    bt = raw.get("block_time")
    try:
        bt_int = int(bt)
    except (TypeError, ValueError):
        return None
    if bt_int <= 0:
        return None

    slot_raw = raw.get("slot")
    try:
        slot_int = int(slot_raw) if slot_raw is not None else 0
    except (TypeError, ValueError):
        slot_int = 0

    sig = raw.get("signature") or ""
    owner = raw.get("owner") or ""
    side = raw.get("side") or "buy"

    if _is_schema_a(raw):
        # Schema A: derive price and vol from reserves + amounts
        vsol = raw.get("virtual_sol_reserves")
        vtok = raw.get("virtual_token_reserves")
        sol_amt = raw.get("sol_amount")
        tok_amt = raw.get("token_amount")
        real_sol = raw.get("real_sol_reserves")

        if not vtok or vtok <= 0 or vsol is None:
            return None

        try:
            price = float(vsol) / float(vtok)
        except (TypeError, ZeroDivisionError):
            return None

        try:
            vol_sol = abs(float(sol_amt or 0)) / 1e9
        except (TypeError, ValueError):
            return None

        # curve_frac: exact from reserves
        try:
            curve_frac_exact = (float(vsol) / 1e9 - _VIRT_OFFSET_SOL) / _CURVE_CAP_SOL
        except (TypeError, ZeroDivisionError):
            curve_frac_exact = None

        try:
            depth_sol = float(real_sol) / 1e9 if real_sol is not None else None
        except (TypeError, ValueError):
            depth_sol = None

        try:
            token_amount_int = int(tok_amt) if tok_amt is not None else None
        except (TypeError, ValueError):
            token_amount_int = None

        return NormRow(
            mint=mint,
            block_time=bt_int,
            slot=slot_int,
            signature=sig,
            price=price,
            side=side,
            vol_sol=vol_sol,
            vol_usd=0.0,        # schema-A pre rows carry no USD (vol_usd=0 discipline)
            owner=owner,
            phase="pre",        # schema-A tapes are all pre-grad bonding-curve swaps
            curve_frac_exact=curve_frac_exact,
            depth_sol=depth_sol,
            token_amount=token_amount_int,
            received_at=raw.get("received_at"),
        )
    else:
        # Schema B: decoded row (solanatrilly self-tape)
        price = raw.get("price")
        if price is None or float(price) <= 0:
            return None
        vol_sol_raw = raw.get("vol_sol") or raw.get("vol") or 0.0
        try:
            vol_sol = abs(float(vol_sol_raw))
        except (TypeError, ValueError):
            return None

        phase = raw.get("phase") or "pre"
        vol_usd = raw.get("vol_usd") or 0.0

        return NormRow(
            mint=mint,
            block_time=bt_int,
            slot=slot_int,
            signature=sig,
            price=float(price),
            side=side,
            vol_sol=vol_sol,
            vol_usd=float(vol_usd),
            owner=owner,
            phase=phase,
            curve_frac_exact=None,
            depth_sol=None,
            token_amount=None,
            received_at=raw.get("received_at"),
        )


def norm_row_to_swap_dict(row: NormRow) -> dict:
    """Convert a ``NormRow`` back to the internal swap-dict format consumed by
    the existing TapeStore / scoring pipeline.

    The existing consumers (assemble_pregrad_features, LakeReader callers,
    curvestage settler) all operate on plain dicts with these keys.
    """
    d: dict = {
        "mint": row.mint,
        "block_time": row.block_time,
        "slot": row.slot,
        "signature": row.signature,
        "price": row.price,
        "side": row.side,
        "vol": row.vol_sol,
        "vol_sol": row.vol_sol,
        "vol_usd": row.vol_usd,
        "owner": row.owner,
        "phase": row.phase,
    }
    if row.received_at is not None:
        d["received_at"] = row.received_at
    # Schema-A extras — include if present (US-93 balance features, settler)
    if row.curve_frac_exact is not None:
        d["curve_frac_exact"] = row.curve_frac_exact
    if row.depth_sol is not None:
        d["depth_sol"] = row.depth_sol
    if row.token_amount is not None:
        d["token_amount"] = row.token_amount
    return d


# ---- Freshness check ---------------------------------------------------------

def billy_firehose_is_live(
    root: str = BILLY_TAPE_DEFAULT_ROOT,
    max_age_s: int = BILLY_TAPE_MAX_AGE_S,
) -> bool:
    """Return True iff solanaBilly's shared tape has a recently-modified part file.

    Uses ONLY file mtime — no cross-service DB/API read (the operator's requirement).
    The mtime of the live part file on the VPS updates roughly every 30 s (per-write),
    so 600 s (10 min) is a safe threshold: stale = writer wedged or firehose off.

    Args:
        root:      Mount root (``/app/lake/billy_tape`` inside the container).
        max_age_s: How many seconds before the newest mtime is considered stale.

    Returns:
        True  → at least one part file modified within max_age_s.
        False → no part files found OR newest mtime is older than max_age_s.
    """
    pattern = os.path.join(root, "dt=*", "part-*.jsonl.gz")
    parts = glob.glob(pattern)
    if not parts:
        logger.warning(
            "[SHARED_TAPE] %s — no part files found under %s.", STALE_WARNING, root
        )
        return False
    newest_mtime = max(os.path.getmtime(p) for p in parts)
    age_s = time.time() - newest_mtime
    if age_s >= max_age_s:
        logger.warning(
            "[SHARED_TAPE] %s newest part is %.0fs old (threshold %ds).",
            STALE_WARNING, age_s, max_age_s,
        )
        return False
    return True


# ---- Tailing reader ----------------------------------------------------------

class BillyTapeTailer:
    """Tail solanaBilly's shared tape at ``root`` and yield normalised swap dicts.

    Design:
      * Scans ``root/dt=*/part-*.jsonl.gz`` for known + new files.
      * Tracks byte-offset per file so only newly-appended lines are read.
      * Yields ``norm_row_to_swap_dict(row)`` for every parseable schema-A line.
      * Skips + counts unparseable lines (US-81 bad-line discipline: never fatal).
      * Runs inside the asyncio event loop via ``async_iter`` (blocks off-loop are
        wrapped in run_in_executor so the event loop stays alive).

    The ``poll_interval_s`` controls how often the directory is re-scanned for
    new part files (default 5 s — well below solanaBilly's ~45-min rotation).
    """

    def __init__(
        self,
        root: str = BILLY_TAPE_DEFAULT_ROOT,
        poll_interval_s: float = 5.0,
        bad_line_log_interval: int = 100,
    ) -> None:
        self._root = root
        self._poll_interval_s = poll_interval_s
        self._offsets: dict[str, int] = {}   # path -> byte offset of last read
        self.bad_lines: int = 0
        self._bad_line_log_interval = bad_line_log_interval

    # ---- sync helpers (called in executor so they don't block the event loop) --

    def _discover_parts(self) -> list[str]:
        """Return sorted list of part-*.jsonl.gz paths under root."""
        pattern = os.path.join(self._root, "dt=*", "part-*.jsonl.gz")
        return sorted(glob.glob(pattern))

    def _read_new_lines(self, path: str) -> Iterator[dict]:
        """Read newly-appended lines from *path* since the last known offset.

        Tolerates truncated tails on the live part file (gzip.BadGzipFile,
        EOFError, OSError) — yields everything before the error point.
        """
        offset = self._offsets.get(path, 0)
        new_offset = offset
        try:
            with gzip.open(path, "rb") as gz:
                gz.seek(offset)
                while True:
                    try:
                        raw = gz.readline()
                    except (EOFError, OSError, gzip.BadGzipFile):
                        break  # truncated tail of live part
                    if not raw:
                        break
                    new_offset = gz.tell()
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        self.bad_lines += 1
                        if self.bad_lines % self._bad_line_log_interval == 0:
                            logger.warning(
                                "[SHARED_TAPE] bad-line count: %d", self.bad_lines
                            )
                        continue
                    row = normalise_row(parsed)
                    if row is None:
                        # Post-grad row (no mint) or truly invalid — skip + count.
                        self.bad_lines += 1
                        continue
                    yield row
        except (OSError, gzip.BadGzipFile) as exc:
            logger.warning("[SHARED_TAPE] cannot open %s: %s", path, exc)
        finally:
            self._offsets[path] = new_offset

    def poll_once_sync(self) -> list[dict]:
        """Sync poll: scan for new/updated parts, drain new lines, return swap dicts.

        Called from the async layer via run_in_executor.
        """
        rows: list[dict] = []
        for path in self._discover_parts():
            try:
                size = os.path.getsize(path)
            except OSError:
                size = 0
            known_offset = self._offsets.get(path, 0)
            if size <= known_offset:
                continue  # no new bytes
            for row in self._read_new_lines(path):
                rows.append(norm_row_to_swap_dict(row))
        return rows

    async def async_iter(
        self,
        stop_event: asyncio.Event,
        queue: asyncio.Queue,
    ) -> None:  # pragma: no cover — genuine I/O loop; unit tests mock poll_once_sync
        """Async loop: poll for new lines, put swap dicts onto *queue*.

        Runs until *stop_event* is set.  Blocks via run_in_executor so the event
        loop stays responsive.
        """
        loop = asyncio.get_event_loop()
        while not stop_event.is_set():
            # Check freshness before each poll cycle; idle if stale.
            if not billy_firehose_is_live(root=self._root):
                logger.warning("[SHARED_TAPE] idle — %s", STALE_WARNING)
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval_s)
                except asyncio.TimeoutError:
                    pass
                continue
            rows = await loop.run_in_executor(None, self.poll_once_sync)
            for swap in rows:
                await queue.put(swap)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                pass
