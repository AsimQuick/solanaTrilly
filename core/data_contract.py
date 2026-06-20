# ---
# module: core.data_contract
# sprint: sprint-14
# story: US-78 AC-78.1 (swaps), AC-78.2 (tokens)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pyarrow, hashlib, json, pathlib, core.tape.manifest
# ---
"""US-78 data-contract export — Parquet surfaces consumed by the solanatrills lab.

The lab (offline training) consumes what this pipeline produces.  Directives §10
defines the contract: **Parquet**, partitioned by **UTC date**, filterable by
date-range + **by-mint + by-wallet(signer)**.  CSV / ``tokens_complete`` is
DEPRECATED and intentionally NOT built here.

Same normalization as live scoring (§2): ``vol_sol`` is the SOL leg, ``sol_usd``
the single USD input; ``usd_value = sol_amount × sol_usd``.  This module performs
**no new feature math** — it is a faithful projection of already-persisted state
(the raw lake and the Token registry) into the agreed column schema.

Surfaces (this module covers 1 + 2; predictions_positions is increment 2):

  (1) ``swaps``  — the firehose tape (drives BOTH the pre-grad model and
      copy-trade).  One row per swap.  Partition ``date(block_time_s)``; sorted
      within each partition by ``(block_time_s, tx_signature, ix_index)``.  The
      ``signer`` column is the copy-trade denominator (queryable across ALL mints,
      including non-graduates).

  (2) ``tokens`` — graduation registry + anchors (IS the graduated-token list
      copy-trade needs, and carries the ``deployer`` that wires the deployer
      features — directives §9/A3).  One row per token.  Partition
      ``date(graduated_time_s)``.

Layout (Hive-style, one part-file per date partition so a date-range scan prunes
whole directories)::

    {out_dir}/{surface}/dt=YYYY-MM-DD/part-0.parquet
    {out_dir}/{surface}/MANIFEST.json

Known source gaps (emitted as null, surfaced in the manifest ``notes`` and
relayed to the lab rather than silently dropped):
  - swaps.ix_index — the per-tx instruction index is not persisted on the Swap
    model / lake row.  It is the *tertiary* sort key only (the parity primary +
    secondary keys, block_time_s + tx_signature, are exact), so its absence does
    not perturb the (block_time, signature) ordering the live scorer already uses
    (US-76 BREAK-2).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq

from core.tape.manifest import (
    MANIFEST_FILENAME,
    build_manifest,
    lc_all_c_sort,
)

# Quote leg is WSOL on pump.fun / PumpSwap; price is SOL-per-token (quote/base).
WSOL_MINT = "So11111111111111111111111111111111111111112"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_date_str(block_time_s: int | float) -> str:
    """Return the ``YYYY-MM-DD`` UTC partition date for a unix-seconds timestamp."""
    return _dt.datetime.fromtimestamp(int(block_time_s), tz=_dt.timezone.utc).strftime(
        "%Y-%m-%d"
    )


def swap_source_from_phase(phase: str | None, lake_source: str | None = None) -> str:
    """Map a tape row's lifecycle phase to the AMM/venue the lab expects.

    Directives §10 wants ``source`` in {pump_dot_fun, pump_amm}.  Pre-graduation
    swaps execute on the pump.fun bonding curve (``pump_dot_fun``); post-graduation
    swaps execute on the PumpSwap AMM (``pump_amm``).  The lake row's own
    ``source`` field is the *data provider* (birdeye/helius), not the venue, so we
    derive the venue from ``phase``.
    """
    if phase == "post":
        return "pump_amm"
    if phase == "pre":
        return "pump_dot_fun"
    # Unknown phase: fall back to the provider tag so nothing is silently coerced.
    return lake_source or "unknown"


def _logical_content_hash(records: list[dict], columns: list[str]) -> str:
    """SHA-256 over a canonical projection of the rows — cross-machine stable.

    Parquet container bytes are not reproducible across machines (compression,
    metadata ordering), so the manifest ``content_hash`` is computed over the
    logical content: each row reduced to ``columns`` in order, serialised as
    canonical JSON, joined by newline.  Two runs over identical data produce an
    identical hash regardless of the parquet writer.
    """
    h = hashlib.sha256()
    for rec in records:
        projected = [rec.get(c) for c in columns]
        h.update(
            json.dumps(projected, separators=(",", ":"), default=str).encode("utf-8")
        )
        h.update(b"\n")
    return h.hexdigest()


def _write_partitioned(
    *,
    records: list[dict],
    schema: pa.Schema,
    partition_key: str,
    surface_dir: Path,
) -> tuple[int, dict[str, int]]:
    """Write ``records`` as ``dt=DATE/part-0.parquet`` files under ``surface_dir``.

    Groups rows by their ``partition_key`` value (a ``YYYY-MM-DD`` string already
    present on each record) and writes one parquet part per date.  Returns the
    total row count and a ``{date: row_count}`` breakdown.
    """
    surface_dir.mkdir(parents=True, exist_ok=True)
    by_date: dict[str, list[dict]] = {}
    for rec in records:
        by_date.setdefault(rec[partition_key], []).append(rec)

    per_partition: dict[str, int] = {}
    field_names = [f.name for f in schema]
    for date_str in sorted(by_date):
        rows = by_date[date_str]
        columns = {name: [r.get(name) for r in rows] for name in field_names}
        table = pa.table(columns, schema=schema)
        part_dir = surface_dir / f"dt={date_str}"
        part_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, part_dir / "part-0.parquet")
        per_partition[date_str] = len(rows)
    return sum(per_partition.values()), per_partition


# ---------------------------------------------------------------------------
# Surface (1) — swaps
# ---------------------------------------------------------------------------

#: Column order is the contract surface order; the sort key is
#: (block_time_s, tx_signature, ix_index).
SWAPS_COLUMNS: list[str] = [
    "mint",
    "block_time_s",
    "slot",
    "tx_signature",
    "ix_index",
    "signer",
    "side",
    "base_amount_raw",
    "base_decimals",
    "base_amount_ui",
    "sol_amount",
    "sol_usd",
    "usd_value",
    "token_price",
    "source",
]

SWAPS_SCHEMA = pa.schema(
    [
        ("mint", pa.string()),
        ("block_time_s", pa.int64()),
        ("slot", pa.int64()),
        ("tx_signature", pa.string()),
        ("ix_index", pa.int32()),  # nullable — not persisted (see module docstring)
        ("signer", pa.string()),  # = owner; the copy-trade denominator, queryable
        ("side", pa.string()),
        ("base_amount_raw", pa.int64()),
        ("base_decimals", pa.int8()),
        ("base_amount_ui", pa.float64()),
        ("sol_amount", pa.float64()),
        ("sol_usd", pa.float64()),
        ("usd_value", pa.float64()),
        ("token_price", pa.float64()),
        ("source", pa.string()),
    ]
)


def _is_finite_pos(x: Any) -> bool:
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return False
    return xf == xf and xf not in (float("inf"), float("-inf")) and xf > 0.0


def _project_swap_row(row: dict, mint_decimals: dict[str, int]) -> dict | None:
    """Project a raw lake row into the ``swaps`` surface schema.

    Returns ``None`` for rows lacking the parity sort keys (block_time/signature)
    — they cannot be placed deterministically and are excluded (counted by caller).
    """
    block_time = row.get("block_time")
    signature = row.get("signature")
    mint = row.get("mint")
    if block_time is None or signature is None or not mint:
        return None

    vol_sol = row.get("vol_sol")
    price = row.get("price")
    decimals = mint_decimals.get(mint)

    # base_amount_ui = token units traded = SOL leg / (SOL per token).  Derived
    # only when price is a finite positive (else null — never a divide-by-zero or
    # a misleading 0).
    base_amount_ui: float | None = None
    if vol_sol is not None and _is_finite_pos(price):
        base_amount_ui = float(vol_sol) / float(price)

    base_amount_raw: int | None = None
    if base_amount_ui is not None and decimals is not None:
        base_amount_raw = int(round(base_amount_ui * (10 ** int(decimals))))

    def _f(v: Any) -> float | None:
        if v is None:
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    return {
        "mint": str(mint),
        "block_time_s": int(block_time),
        "slot": int(row["slot"]) if row.get("slot") is not None else None,
        "tx_signature": str(signature),
        "ix_index": None,  # not persisted — tertiary sort key only
        "signer": (str(row["owner"]) if row.get("owner") else None),
        "side": row.get("side"),
        "base_amount_raw": base_amount_raw,
        "base_decimals": (int(decimals) if decimals is not None else None),
        "base_amount_ui": base_amount_ui,
        "sol_amount": _f(vol_sol),
        "sol_usd": _f(row.get("sol_usd")),
        "usd_value": _f(row.get("vol_usd")),
        "token_price": _f(price),
        "source": swap_source_from_phase(row.get("phase"), row.get("source")),
        # partition helper (stripped from the parquet columns via schema projection)
        "_dt": utc_date_str(int(block_time)),
    }


def build_swaps_dataset(
    lake_rows: Iterable[dict],
    *,
    out_dir: str | Path,
    mint_decimals: dict[str, int] | None = None,
    dataset_id: str = "data_contract_swaps",
) -> dict[str, Any]:
    """Build the ``swaps`` Parquet surface from raw lake rows (US-78 AC-78.1).

    Args:
        lake_rows: Iterable of NormalizedSwap lake dicts (from ``LakeReader``).
        out_dir: Export root; the surface is written under ``{out_dir}/swaps/``.
        mint_decimals: ``{mint: base_decimals}`` map (from the Token registry) used
            to derive ``base_amount_raw``.  Missing mints → null decimals/raw.
        dataset_id: Manifest dataset identifier.

    Returns:
        ``{"surface", "path", "row_count", "skipped", "partitions",
           "manifest", "manifest_path", "columns"}``.
    """
    mint_decimals = mint_decimals or {}
    surface_dir = Path(out_dir) / "swaps"

    records: list[dict] = []
    skipped = 0
    for row in lake_rows:
        projected = _project_swap_row(row, mint_decimals)
        if projected is None:
            skipped += 1
            continue
        records.append(projected)

    # Contract sort: (block_time_s, tx_signature, ix_index).  ix_index is null, so
    # ("" if None) keeps it stable and last — matching the live (block_time,
    # signature) ordering (US-76 BREAK-2).
    records.sort(
        key=lambda r: (r["block_time_s"], r["tx_signature"] or "", r["ix_index"] or 0)
    )

    row_count, per_partition = _write_partitioned(
        records=records,
        schema=SWAPS_SCHEMA,
        partition_key="_dt",
        surface_dir=surface_dir,
    )

    return _finalise_surface(
        surface="swaps",
        surface_dir=surface_dir,
        records=records,
        columns=SWAPS_COLUMNS,
        mint_field="mint",
        dataset_id=dataset_id,
        source="lake://lake/tapes",
        row_count=row_count,
        per_partition=per_partition,
        skipped=skipped,
        notes=[
            "ix_index is null (not persisted); it is the tertiary sort key only — "
            "the (block_time_s, tx_signature) parity order is exact.",
            "base_amount_ui = vol_sol / token_price (token units); base_amount_raw "
            "= round(base_amount_ui * 10^base_decimals) when decimals are known.",
            "source derived from tape phase (pre->pump_dot_fun, post->pump_amm).",
        ],
    )


# ---------------------------------------------------------------------------
# Surface (2) — tokens
# ---------------------------------------------------------------------------

TOKENS_COLUMNS: list[str] = [
    "mint",
    "creation_time_s",
    "graduated_time_s",
    "deployer",
    "symbol",
    "base_decimals",
    "source",
    "graduated",
    "progress_percent",
]

TOKENS_SCHEMA = pa.schema(
    [
        ("mint", pa.string()),
        ("creation_time_s", pa.int64()),
        ("graduated_time_s", pa.int64()),
        ("deployer", pa.string()),
        ("symbol", pa.string()),
        ("base_decimals", pa.int8()),
        ("source", pa.string()),
        ("graduated", pa.bool_()),
        ("progress_percent", pa.float64()),
    ]
)


def _dig(d: Any, *path: str) -> Any:
    """Safely walk nested dict keys; return None on any miss/non-dict."""
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def project_token_row(token: dict) -> dict | None:
    """Project a Token registry record into the ``tokens`` surface schema.

    ``token`` is a plain dict with at least ``mint``, ``graduated_block_time``,
    ``dex_source`` and ``raw_graduation`` (the verbatim MEME_DATA event).  The
    deployer / symbol / decimals / creation-time / progress live inside
    ``raw_graduation`` (Birdeye meme_stats); each is dug out defensively.
    """
    mint = token.get("mint")
    grad_t = token.get("graduated_block_time")
    if not mint or grad_t is None:
        return None

    rg = token.get("raw_graduation") or {}
    raw = rg.get("raw") or {}

    creation = rg.get("creation_time")
    deployer = _dig(raw, "meme_info", "creator") or _dig(rg, "meme_info", "creator")
    symbol = raw.get("symbol") or rg.get("symbol")
    decimals = rg.get("decimals")
    if decimals is None:
        decimals = raw.get("decimals")
    source = rg.get("source") or token.get("dex_source")
    graduated = rg.get("graduated")
    if graduated is None:
        graduated = True  # presence in the registry = graduated
    progress = rg.get("progress_percent")

    def _int_or_none(v: Any) -> int | None:
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return {
        "mint": str(mint),
        "creation_time_s": _int_or_none(creation),
        "graduated_time_s": int(grad_t),
        "deployer": (str(deployer) if deployer else None),
        "symbol": (str(symbol) if symbol else None),
        "base_decimals": _int_or_none(decimals),
        "source": (str(source) if source else None),
        "graduated": bool(graduated),
        "progress_percent": (
            float(progress) if isinstance(progress, (int, float)) else None
        ),
        "_dt": utc_date_str(int(grad_t)),
    }


def build_tokens_dataset(
    tokens: Iterable[dict],
    *,
    out_dir: str | Path,
    dataset_id: str = "data_contract_tokens",
) -> dict[str, Any]:
    """Build the ``tokens`` Parquet surface from Token registry rows (US-78 AC-78.2)."""
    surface_dir = Path(out_dir) / "tokens"

    records: list[dict] = []
    skipped = 0
    for tok in tokens:
        projected = project_token_row(tok)
        if projected is None:
            skipped += 1
            continue
        records.append(projected)

    records.sort(key=lambda r: (r["graduated_time_s"], r["mint"]))

    row_count, per_partition = _write_partitioned(
        records=records,
        schema=TOKENS_SCHEMA,
        partition_key="_dt",
        surface_dir=surface_dir,
    )

    return _finalise_surface(
        surface="tokens",
        surface_dir=surface_dir,
        records=records,
        columns=TOKENS_COLUMNS,
        mint_field="mint",
        dataset_id=dataset_id,
        source="db://core.Token",
        row_count=row_count,
        per_partition=per_partition,
        skipped=skipped,
        notes=[
            "deployer = raw_graduation.raw.meme_info.creator (wires deployer features).",
            "creation_time_s / symbol / base_decimals / progress_percent are dug "
            "from raw_graduation; null when the MEME_DATA event omitted them.",
        ],
    )


# ---------------------------------------------------------------------------
# Manifest finalisation (shared)
# ---------------------------------------------------------------------------


def _finalise_surface(
    *,
    surface: str,
    surface_dir: Path,
    records: list[dict],
    columns: list[str],
    mint_field: str,
    dataset_id: str,
    source: str,
    row_count: int,
    per_partition: dict[str, int],
    skipped: int,
    notes: list[str],
) -> dict[str, Any]:
    """Write the surface MANIFEST.json and return the export result dict."""
    dates = sorted(per_partition)
    date_start = dates[0] if dates else ""
    date_end = dates[-1] if dates else ""
    mint_cohort = lc_all_c_sort(
        list({r.get(mint_field) for r in records if r.get(mint_field)})
    )

    manifest = build_manifest(
        dataset_id=dataset_id,
        source=source,
        date_start=date_start,
        date_end=date_end,
        mint_cohort=mint_cohort,
        row_count=row_count,
        content_hash=_logical_content_hash(records, columns),
    )
    # Contract-specific annotations (additive — never override the required keys).
    manifest["surface"] = surface
    manifest["format"] = "parquet"
    manifest["partition_scheme"] = "dt=YYYY-MM-DD (UTC)"
    manifest["columns"] = columns
    manifest["partitions"] = per_partition
    manifest["skipped_rows"] = skipped
    manifest["notes"] = notes

    surface_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = surface_dir / MANIFEST_FILENAME
    with manifest_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    return {
        "surface": surface,
        "path": str(surface_dir),
        "row_count": row_count,
        "skipped": skipped,
        "partitions": per_partition,
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "columns": columns,
    }
