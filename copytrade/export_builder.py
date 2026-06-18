# ---
# module: copytrade.export_builder
# sprint: sprint-12
# story: US-63 AC-63.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.models, core.tape.manifest, csv, pathlib
# ---
"""Copytrade positions export — CSV + MANIFEST for the click-to-download export (SPEC §11, AC-63.3).

Mirrors the §6.5 Feature Builder / annotation_export pattern:
  CSV output      — one row per CopytradePosition in the cohort (all positions: open + closed)
  MANIFEST.json   — dataset_id, content_hash (SHA-256), mint_cohort (LC_ALL=C sorted),
                    row_count, source, date_range  (per US-36 / core.tape.manifest)

Runs as a Celery task (copytrade.tasks.export_copytrade_positions) via the EXISTING §6.5
export channel — NEVER on web/gunicorn (#289).
The raw lake is NEVER touched (raw=immutable, §6.4.1).  No new PnL/price math.
"""
from __future__ import annotations

import csv
from pathlib import Path


def build_copytrade_export(
    cohort_id: str,
    output_path: str,
    *,
    dataset_id: str = "copytrade_export",
) -> dict:
    """Write a CSV of all CopytradePosition rows for *cohort_id* plus a MANIFEST.

    Includes BOTH open and closed positions so research can compare live results
    to the offline precision baseline (SPEC §11 requirement).

    CSV columns:
      id, cohort_id, mint, trigger_wallet, mode, status,
      entry_ts, entry_price, sol_in,
      exit_ts, exit_price, sol_out, exit_reason,
      realized_pnl_sol, realized_pnl_pct

    The MANIFEST follows the US-36 pattern (core.tape.manifest):
      dataset_id, content_hash (SHA-256 of raw CSV bytes), mint_cohort
      (LC_ALL=C-sorted unique mints), row_count, source="copytrade_positions",
      date_range (min/max entry_ts, or empty strings when no positions).

    No new PnL/price math — serialises the copytrade_ rows directly (Principle #2).

    Args:
        cohort_id:   Cohort to export (all positions for this cohort_id).
        output_path: Destination CSV file path.
        dataset_id:  Unique identifier placed in the MANIFEST dataset_id field.

    Returns:
        {"path": str, "manifest": dict, "manifest_path": str, "row_count": int}
    """
    from copytrade.models import CopytradePosition
    from core.tape.manifest import build_manifest, compute_content_hash, lc_all_c_sort, write_manifest

    qs = CopytradePosition.objects.filter(cohort_id=cohort_id).order_by("entry_ts")
    positions = list(qs.values(
        "id",
        "cohort_id",
        "mint",
        "trigger_wallet",
        "mode",
        "status",
        "entry_ts",
        "entry_price",
        "sol_in",
        "exit_ts",
        "exit_price",
        "sol_out",
        "exit_reason",
        "realized_pnl_sol",
        "realized_pnl_pct",
    ))

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "id", "cohort_id", "mint", "trigger_wallet", "mode", "status",
        "entry_ts", "entry_price", "sol_in",
        "exit_ts", "exit_price", "sol_out", "exit_reason",
        "realized_pnl_sol", "realized_pnl_pct",
    ]

    rows = []
    for pos in positions:
        rows.append({
            "id": pos["id"],
            "cohort_id": pos["cohort_id"],
            "mint": pos["mint"],
            "trigger_wallet": pos["trigger_wallet"],
            "mode": pos["mode"],
            "status": pos["status"],
            "entry_ts": pos["entry_ts"].isoformat() if pos["entry_ts"] else "",
            "entry_price": pos["entry_price"],
            "sol_in": pos["sol_in"],
            "exit_ts": pos["exit_ts"].isoformat() if pos["exit_ts"] else "",
            "exit_price": pos["exit_price"] if pos["exit_price"] is not None else "",
            "sol_out": pos["sol_out"] if pos["sol_out"] is not None else "",
            "exit_reason": pos["exit_reason"] if pos["exit_reason"] else "",
            "realized_pnl_sol": pos["realized_pnl_sol"] if pos["realized_pnl_sol"] is not None else "",
            "realized_pnl_pct": pos["realized_pnl_pct"] if pos["realized_pnl_pct"] is not None else "",
        })

    with open(output_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    content_hash = compute_content_hash(Path(output_path))

    unique_mints = sorted({p["mint"] for p in positions if p["mint"]})
    mint_cohort = lc_all_c_sort(unique_mints)

    entry_timestamps = [p["entry_ts"] for p in positions if p["entry_ts"]]
    if entry_timestamps:
        sorted_ts = sorted(entry_timestamps)
        date_start = sorted_ts[0].strftime("%Y-%m-%d")
        date_end = sorted_ts[-1].strftime("%Y-%m-%d")
    else:
        date_start = ""
        date_end = ""

    manifest = build_manifest(
        dataset_id=dataset_id,
        source="copytrade_positions",
        date_start=date_start,
        date_end=date_end,
        mint_cohort=list(mint_cohort),
        row_count=len(rows),
        content_hash=content_hash,
    )

    manifest_path = write_manifest(Path(output_path).parent, manifest)

    return {
        "path": str(output_path),
        "manifest": manifest,
        "manifest_path": str(manifest_path),
        "row_count": len(rows),
    }
