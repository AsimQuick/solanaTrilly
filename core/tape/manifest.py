# ---
# module: core.tape.manifest
# sprint: sprint-8
# story: US-36 AC-36.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: gzip, hashlib, json, pathlib, typing
# ---
"""LakeManifest — MANIFEST writer/reader for the data lake (data-lake.md §1).

Every dataset that enters the lake must carry a MANIFEST so that cross-machine
and cross-run comparisons are reliable.  The MANIFEST carries:

  dataset_id   : str  — unique identifier for the dataset partition
  source       : str  — data source tag (helius_live, birdeye_live, etc.)
  date_range   : dict — {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}
  mint_cohort  : list — LC_ALL=C-sorted mint addresses present in the dataset
  row_count    : int  — total row count (decompressed lines for .jsonl.gz)
  content_hash : str  — SHA-256 hex digest of the decompressed content

Cross-machine consistency:
  mint_cohort is sorted with byte-order (ASCII) comparison, equivalent to
  LC_ALL=C sort on Unix.  Solana base58 addresses are all printable ASCII so
  this is identical to `LC_ALL=C sort` and avoids macOS/Linux ICU divergence.

  content_hash is computed over the DECOMPRESSED bytes of .jsonl.gz files so
  that different gzip compression settings on different machines never produce
  a false hash mismatch (the logical content is stable; the container is not).
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_FILENAME: str = "MANIFEST.json"

#: Required top-level keys in every valid MANIFEST
MANIFEST_REQUIRED_KEYS: frozenset[str] = frozenset(
    {"dataset_id", "source", "date_range", "mint_cohort", "row_count", "content_hash"}
)


# ---------------------------------------------------------------------------
# Sort — cross-machine LC_ALL=C byte-order determinism
# ---------------------------------------------------------------------------


def lc_all_c_sort(strings: list[str]) -> list[str]:
    """Sort strings using byte-order (LC_ALL=C) — cross-machine deterministic.

    Python's default ``str`` sort uses Unicode code-point order, which matches
    C locale byte ordering for printable ASCII.  Solana base58 addresses are
    all printable ASCII characters, so sorting by ``s.encode("ascii")`` is
    byte-for-byte identical to ``LC_ALL=C sort`` on Unix.

    Args:
        strings: List of strings to sort (typically Solana mint addresses).

    Returns:
        New sorted list using ASCII byte order.
    """
    return sorted(strings, key=lambda s: s.encode("ascii", errors="replace"))


# ---------------------------------------------------------------------------
# Content hash — decompressed SHA-256 for cross-platform determinism
# ---------------------------------------------------------------------------


def compute_content_hash(file_path: Path) -> str:
    """Return the SHA-256 hex digest of *file_path*'s logical content.

    For ``.jsonl.gz`` files the hash is computed over the DECOMPRESSED bytes
    so that different gzip compression levels or implementations on different
    machines produce the same hash for the same logical data.

    For all other file types the hash is computed over the raw file bytes.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Lowercase hex SHA-256 digest string (64 characters).
    """
    file_path = Path(file_path)
    h = hashlib.sha256()
    if file_path.name.endswith(".jsonl.gz"):
        with gzip.open(file_path, "rb") as gz:
            for chunk in iter(lambda: gz.read(65536), b""):
                h.update(chunk)
    else:
        with file_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Build — construct a validated manifest dict
# ---------------------------------------------------------------------------


def build_manifest(
    *,
    dataset_id: str,
    source: str,
    date_start: str,
    date_end: str,
    mint_cohort: list[str],
    row_count: int,
    content_hash: str,
) -> dict[str, Any]:
    """Build and return a MANIFEST dict ready for ``write_manifest()``.

    mint_cohort is LC_ALL=C-sorted before being stored so cross-machine
    comparisons never diverge due to locale collation differences.

    Args:
        dataset_id:    Unique identifier for this dataset partition.
        source:        Data source tag (e.g. ``"helius_live"``).
        date_start:    First date in the dataset (``"YYYY-MM-DD"``).
        date_end:      Last date in the dataset (``"YYYY-MM-DD"``).
        mint_cohort:   List of mint addresses covered by this dataset.
        row_count:     Number of data rows.
        content_hash:  SHA-256 hex digest of the data file's logical content.

    Returns:
        Dict with the standard MANIFEST schema fields.
    """
    return {
        "dataset_id": dataset_id,
        "source": source,
        "date_range": {"start": date_start, "end": date_end},
        "mint_cohort": lc_all_c_sort(mint_cohort),
        "row_count": row_count,
        "content_hash": content_hash,
    }


# ---------------------------------------------------------------------------
# Write / Read
# ---------------------------------------------------------------------------


def write_manifest(directory: Path, manifest: dict[str, Any]) -> Path:
    """Write *manifest* as MANIFEST.json inside *directory*.

    Creates *directory* (and any missing parents) if it does not already exist.
    The JSON is written with ``sort_keys=True`` and ``indent=2`` so the file is
    human-readable and diff-friendly.

    Args:
        directory: The partition directory that contains (or will contain) the
                   data file.
        manifest:  A manifest dict, typically produced by ``build_manifest()``.

    Returns:
        Absolute path to the written MANIFEST.json file.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out_path = directory / MANIFEST_FILENAME
    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return out_path


def read_manifest(directory: Path) -> dict[str, Any]:
    """Read and return the MANIFEST.json from *directory*.

    Args:
        directory: The partition directory that contains MANIFEST.json.

    Returns:
        Parsed MANIFEST dict.

    Raises:
        FileNotFoundError: if MANIFEST.json does not exist in *directory*.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    manifest_path = Path(directory) / MANIFEST_FILENAME
    with manifest_path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def manifest_path(directory: Path) -> Path:
    """Return the canonical MANIFEST.json path for *directory* (may not exist)."""
    return Path(directory) / MANIFEST_FILENAME
