# ---
# module: core.data_contract
# sprint: sprint-14
# story: US-78 AC-78.1 (swaps), AC-78.2 (tokens); fix/us78-export-from-lake
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-21
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

#: Minimum plausible Solana block_time (2020-01-01T00:00:00Z).  Rows with a
#: block_time at or below this value (or non-positive / None) are treated as
#: bad-timestamp rows and excluded from the export — they must not create
#: pre-2020 dt= partitions (the dt=1977 / dt=1970 bucket bug).
_MIN_VALID_BLOCK_TIME: int = 1_577_836_800  # 2020-01-01 00:00:00 UTC


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

    Returns ``None`` for rows that:
    - lack the parity sort keys (block_time / signature) — they cannot be placed
      deterministically and are excluded (counted by caller); or
    - have an implausible block_time (<= ``_MIN_VALID_BLOCK_TIME``, i.e. pre-2020)
      — these are bad-timestamp rows (epoch-zero, raw Solana slot numbers, Birdeye
      parse failures) that would create junk dt=1970/1977/… partitions.
    """
    block_time = row.get("block_time")
    signature = row.get("signature")
    mint = row.get("mint")
    if block_time is None or signature is None or not mint:
        return None
    try:
        bt_int = int(block_time)
    except (TypeError, ValueError):
        return None
    if bt_int <= _MIN_VALID_BLOCK_TIME:
        return None  # pre-2020 / epoch-zero / slot-sized bad timestamp — drop

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
        "block_time_s": bt_int,
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
        "_dt": utc_date_str(bt_int),
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
# Surface (3) — predictions_positions
# ---------------------------------------------------------------------------

#: Per-label projection order.  The scorer's labels are carried in the Prediction
#: JSON; the contract names the three v3.2 labels explicitly.
PRED_LABELS: tuple[str, str, str] = ("ctrl", "oracle", "liq")

PREDICTIONS_POSITIONS_COLUMNS: list[str] = [
    "mint",
    "score_time_s",
    "ctrl_pred",
    "oracle_pred",
    "liq_pred",
    "blend",
    "percentile",
    "picked",
    "per_day_target",
    "entry_time_s",
    "entry_price",
    "entry_sol_usd",
    "size_usd",
    "fill_status",
    "slippage_bps",
    "exit_time_s",
    "exit_price",
    "exit_reason",
    "realized_pnl_usd",
    "watched_wallet",
    "trigger_buy_usd",
    "copy_latency_s",
]

PREDICTIONS_POSITIONS_SCHEMA = pa.schema(
    [
        ("mint", pa.string()),
        ("score_time_s", pa.int64()),
        ("ctrl_pred", pa.float64()),
        ("oracle_pred", pa.float64()),
        ("liq_pred", pa.float64()),
        ("blend", pa.float64()),
        ("percentile", pa.float64()),
        ("picked", pa.bool_()),
        ("per_day_target", pa.int32()),
        ("entry_time_s", pa.int64()),
        ("entry_price", pa.float64()),
        ("entry_sol_usd", pa.float64()),
        ("size_usd", pa.float64()),
        ("fill_status", pa.string()),
        ("slippage_bps", pa.float64()),
        ("exit_time_s", pa.int64()),
        ("exit_price", pa.float64()),
        ("exit_reason", pa.string()),
        ("realized_pnl_usd", pa.float64()),
        ("watched_wallet", pa.string()),
        ("trigger_buy_usd", pa.float64()),
        ("copy_latency_s", pa.float64()),
    ]
)


def _to_epoch_s(v: Any) -> int | None:
    """Convert a datetime (or already-int unix seconds) to int unix seconds."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    try:
        return int(v.timestamp())  # datetime
    except (AttributeError, TypeError, ValueError):
        return None


def _f_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _project_model_prediction_row(pred: dict, position: dict | None) -> dict:
    """One predictions_positions row from a Prediction left-joined to its Position."""
    label_scores = pred.get("label_scores") or {}
    sol_usd = _f_or_none(pred.get("sol_usd_spot"))
    score_time = int(pred["score_time"])

    entry_time = entry_price = size_usd = None
    exit_time = exit_price = exit_reason = realized_pnl_usd = None
    fill_status = "PICKED_NO_POSITION" if pred.get("picked") else "NOT_PICKED"
    if position is not None:
        entry_time = _to_epoch_s(position.get("entry_ts"))
        entry_price = _f_or_none(position.get("entry_price"))
        size_sol = _f_or_none(position.get("size_sol"))
        if size_sol is not None and sol_usd is not None:
            size_usd = size_sol * sol_usd
        exit_time = _to_epoch_s(position.get("exit_ts"))
        exit_price = _f_or_none(position.get("exit_price"))
        exit_reason = position.get("exit_trigger")
        pnl_sol = _f_or_none(position.get("realized_pnl_sol"))
        if pnl_sol is not None and sol_usd is not None:
            realized_pnl_usd = pnl_sol * sol_usd
        fill_status = position.get("status") or "FILLED"

    return {
        "mint": str(pred["mint"]),
        "score_time_s": score_time,
        "ctrl_pred": _f_or_none(label_scores.get("ctrl")),
        "oracle_pred": _f_or_none(label_scores.get("oracle")),
        "liq_pred": _f_or_none(label_scores.get("liq")),
        "blend": _f_or_none(pred.get("blend")),
        "percentile": _f_or_none(pred.get("blend")),  # blend IS the percentile-space score
        "picked": bool(pred.get("picked")),
        "per_day_target": (
            int(pred["per_day_target"]) if pred.get("per_day_target") is not None else None
        ),
        "entry_time_s": entry_time,
        "entry_price": entry_price,
        "entry_sol_usd": sol_usd,
        "size_usd": size_usd,
        "fill_status": fill_status,
        "slippage_bps": None,  # not modelled on the paper leg
        "exit_time_s": exit_time,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "realized_pnl_usd": realized_pnl_usd,
        "watched_wallet": None,
        "trigger_buy_usd": None,
        "copy_latency_s": None,
        "_dt": utc_date_str(score_time),
    }


def _project_copy_position_row(cp: dict) -> dict | None:
    """One predictions_positions row for a copy-trade position (no model score)."""
    entry_time = _to_epoch_s(cp.get("entry_ts"))
    # Copy positions have no model score_time; anchor the row at entry (or exit).
    anchor = entry_time or _to_epoch_s(cp.get("exit_ts"))
    if anchor is None:
        return None
    pnl_sol = _f_or_none(cp.get("realized_pnl_sol"))
    cap_pct = _f_or_none(cp.get("cap_pct"))

    return {
        "mint": str(cp.get("mint")),
        "score_time_s": int(anchor),
        "ctrl_pred": None,
        "oracle_pred": None,
        "liq_pred": None,
        "blend": None,
        "percentile": None,
        "picked": True,  # a copy position exists => the wallet trade was mirrored
        "per_day_target": None,
        "entry_time_s": entry_time,
        "entry_price": _f_or_none(cp.get("entry_price")),
        "entry_sol_usd": None,  # copy positions do not carry the serving spot
        "size_usd": _f_or_none(cp.get("size_usd")),
        "fill_status": (cp.get("status") or "").upper() or None,
        "slippage_bps": (cap_pct * 10000.0 if cap_pct is not None else None),
        "exit_time_s": _to_epoch_s(cp.get("exit_ts")),
        "exit_price": _f_or_none(cp.get("exit_price")),
        "exit_reason": cp.get("exit_reason"),
        "realized_pnl_usd": pnl_sol,  # SOL-space; USD basis not carried on copy rows
        "watched_wallet": cp.get("trigger_wallet"),
        "trigger_buy_usd": None,  # the watched wallet's buy size is not persisted
        "copy_latency_s": _f_or_none(cp.get("copy_latency_s")),
        "_dt": utc_date_str(int(anchor)),
    }


def build_predictions_positions_dataset(
    predictions: Iterable[dict],
    model_positions: Iterable[dict],
    copy_positions: Iterable[dict] | None = None,
    *,
    out_dir: str | Path,
    dataset_id: str = "data_contract_predictions_positions",
) -> dict[str, Any]:
    """Build the ``predictions_positions`` surface (US-78, §10).

    Joins each model ``Prediction`` (score breakdown) to its model ``Position``
    (entry/exit) by mint — the nearest position whose entry is at/after the
    score_time — and appends copy-trade positions (score columns null, copy-variant
    columns populated).

    Args:
        predictions: Prediction rows (model-scored tokens).
        model_positions: trading.Position rows with source='model'.
        copy_positions: copytrade.CopytradePosition rows (optional).
        out_dir / dataset_id: as for the other surfaces.
    """
    surface_dir = Path(out_dir) / "predictions_positions"

    # Index model positions by mint (sorted by entry) for the nearest-after join.
    pos_by_mint: dict[str, list[dict]] = {}
    for p in model_positions:
        pos_by_mint.setdefault(p.get("mint"), []).append(p)
    for plist in pos_by_mint.values():
        plist.sort(key=lambda p: _to_epoch_s(p.get("entry_ts")) or 0)

    records: list[dict] = []
    skipped = 0
    for pred in predictions:
        if pred.get("mint") is None or pred.get("score_time") is None:
            skipped += 1
            continue
        score_time = int(pred["score_time"])
        # Nearest position entered at/after score_time; else the latest before it.
        candidates = pos_by_mint.get(pred.get("mint"), [])
        chosen = None
        for p in candidates:
            et = _to_epoch_s(p.get("entry_ts"))
            if et is not None and et >= score_time:
                chosen = p
                break
        if chosen is None and candidates:
            chosen = candidates[-1]
        records.append(_project_model_prediction_row(pred, chosen))

    for cp in copy_positions or []:
        row = _project_copy_position_row(cp)
        if row is None:
            skipped += 1
            continue
        records.append(row)

    records.sort(key=lambda r: (r["score_time_s"], r["mint"] or ""))

    row_count, per_partition = _write_partitioned(
        records=records,
        schema=PREDICTIONS_POSITIONS_SCHEMA,
        partition_key="_dt",
        surface_dir=surface_dir,
    )

    return _finalise_surface(
        surface="predictions_positions",
        surface_dir=surface_dir,
        records=records,
        columns=PREDICTIONS_POSITIONS_COLUMNS,
        mint_field="mint",
        dataset_id=dataset_id,
        source="db://core.Prediction+trading.Position+copytrade.CopytradePosition",
        row_count=row_count,
        per_partition=per_partition,
        skipped=skipped,
        notes=[
            "Model rows: Prediction (score breakdown) left-joined to its model "
            "Position by mint (nearest entry at/after score_time).",
            "percentile = blend (the blend IS the cross-sectional percentile-space score).",
            "ctrl/oracle/liq_pred are the raw per-label seed-averaged booster preds "
            "(Prediction.label_scores); null if the model used different label keys.",
            "size_usd = position.size_sol * entry_sol_usd; realized_pnl_usd = "
            "realized_pnl_sol * entry_sol_usd (entry_sol_usd = the serving SOL/USD spot).",
            "slippage_bps null on the model paper leg (not modelled).",
            "Copy rows: score columns null; realized_pnl_usd carries SOL-space pnl "
            "(no USD basis on copy rows); trigger_buy_usd not persisted.",
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
