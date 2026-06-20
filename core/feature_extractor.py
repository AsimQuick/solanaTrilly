# ---
# module: core.feature_extractor
# sprint: sprint-7, sprint-9
# story: US-30 AC-30.1, AC-30.2, AC-30.3, US-41 AC-41.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.tape_microstructure, core.pregrad_features, core.models
# ---
"""FeatureExtractor — ONE deterministic code path for live and offline features.

Principle #2 (PRD §6.4.4 / §6.4.5): The SAME extractor (this module) is the
single import for the live scorer, the offline Feature Builder (US-31), and
the replay harness.  No live-only or offline-only feature assembly exists
outside this module.

Both sources (DB 'swaps' mirror and the jsonl.gz lake) normalize their rows to
the §7.1 swap dict {rel, price, side, vol, owner, block_time, slot, signature},
then pass through the same _compute() method, producing byte-identical feature
values for the same input data — closing the assembly-drift class #358/#359/#367
by construction.

§7.1 microstructure input contract:
  rel:        float  — seconds since graduated_block_time (DB Token anchor)
  price:      float  — > 0; USD or SOL per token (shape features are unit-invariant)
  side:       str    — "buy" | "sell"
  vol:        float  — swap size in quote unit (vol_sol for PumpSwap live path)
  owner:      str|None — signer wallet for unique-trader counts

Sort key (stable, deterministic):
  Swaps are ordered by (block_time, slot, signature) before compute_features.
  Equal-rel swaps preserve this order because compute_features re-sorts by rel
  using Python's stable sort — tiebreaking is deterministic end-to-end.

Lake rows must include a 'mint' field (written alongside the NormalizedSwap
fields) so the extractor can filter to the requested token without re-reading
the entire lake.
"""
from __future__ import annotations

from core.pregrad_features import compute_pregrad_features
from core.tape_microstructure import compute_features


def _db_row_to_micro(swap) -> dict:
    """Normalize a DB Swap model instance to the §7.1 microstructure input dict."""
    return {
        "block_time": swap.block_time,
        "slot": swap.slot,
        "signature": swap.signature,
        "rel": swap.rel,
        "price": swap.price,
        "side": swap.side,
        "vol": swap.vol_sol,
        "owner": swap.owner,
    }


def _lake_row_to_micro(row: dict) -> dict:
    """Normalize a decoded jsonl.gz lake row to the §7.1 microstructure input dict.

    ``rel`` (block_time - graduated_block_time) is absent on AC-3 pre-grad firehose
    tape rows (the live buffer omits it — the graduation anchor is applied
    retroactively), so it is read defensively and defaults to 0.0.  Golden /
    post-grad lakes always carry ``rel``, so this preserves offline feature parity
    (``.get`` returns the real value when present) while letting the dashboard
    candle/cohort APIs read the mixed live lake without crashing.
    """
    return {
        "block_time": int(row["block_time"]),
        "slot": int(row.get("slot", 0) or 0),
        "signature": str(row.get("signature", "")),
        "rel": float(row.get("rel", 0.0) or 0.0),
        "price": float(row.get("price", 0.0) or 0.0),
        "side": str(row.get("side", "")),
        "vol": float(row.get("vol_sol", row.get("vol", 0.0)) or 0.0),
        "owner": row.get("owner"),
    }


class FeatureExtractor:
    """Shared deterministic extractor: raw swaps → §7.1 input → compute_features.

    One code path, two source adapters (DB and lake).  Both adapters normalize
    to the §7.1 dict format, sort by the stable (block_time, slot, signature)
    key, then call the vendored compute_features via the single _compute() method.

    Args:
        feature_set: The FeatureSet this extraction is being performed against.
                     Provides version/hash context; the compute window (window_s)
                     must be passed explicitly (defaults to 120s).
    """

    def __init__(self, feature_set) -> None:
        self._feature_set = feature_set

    # ------------------------------------------------------------------
    # Public API — two source adapters, one compute path
    # ------------------------------------------------------------------

    def extract_from_db(
        self,
        mint: str,
        *,
        window_s: int = 120,
        bucket_s: int = 15,
    ) -> dict | None:
        """Extract features for *mint* from the DB 'swaps' mirror.

        Reads Swap rows for *mint* ordered by (block_time, slot, signature),
        normalizes to §7.1 format, and delegates to _compute().

        Returns:
            Feature dict from compute_features, or None if no usable swaps
            exist within [0, window_s).  None means no-feature — never a zero row.
        """
        swaps = self._load_db_swaps(mint)
        features = self._compute(swaps, window_s=window_s, bucket_s=bucket_s)
        if features is None:
            return None
        return {
            **features,
            "_feature_set_hash": self._feature_set.hash,
            "_math_version": self._feature_set.math_version,
        }

    def extract_from_lake(
        self,
        mint: str,
        rows: list[dict],
        *,
        window_s: int = 120,
        bucket_s: int = 15,
    ) -> dict | None:
        """Extract features for *mint* from pre-decoded jsonl.gz lake rows.

        *rows* is the flat list returned by LakeReader.iter_rows().  Each row
        must carry a 'mint' key alongside the NormalizedSwap fields so this
        method can filter to the requested token.  Rows lacking 'mint' are
        silently skipped (they belong to an unidentified token and cannot be
        attributed to *mint*).

        Normalizes filtered rows to §7.1 format, sorts by (block_time, slot,
        signature), and delegates to _compute() — the same path as extract_from_db.

        Returns:
            Feature dict from compute_features, or None if no usable swaps
            exist within [0, window_s).
        """
        swaps = self._load_lake_swaps(mint, rows)
        features = self._compute(swaps, window_s=window_s, bucket_s=bucket_s)
        if features is None:
            return None
        return {
            **features,
            "_feature_set_hash": self._feature_set.hash,
            "_math_version": self._feature_set.math_version,
        }

    # ------------------------------------------------------------------
    # Pre-graduation (pre_*) buyer-cohort feature extractors (US-41 AC-41.2)
    # ------------------------------------------------------------------

    def extract_pregrad_from_lake(
        self,
        mint: str,
        rows: list[dict],
        *,
        deployer: str | None = None,
    ) -> dict | None:
        """Extract the 20 pre_* buyer-cohort features from pre-graduation lake rows.

        Reuses the same _load_lake_swaps adapter as extract_from_lake, then
        delegates to compute_pregrad_features (which selects only rel < 0 swaps).

        Args:
            mint:     Token mint address to filter.
            rows:     Raw lake rows (same format as extract_from_lake accepts).
            deployer: Optional deployer wallet for pre_deployer_* features.

        Returns:
            Dict of 20 pre_* features with feature-set stamps, or None if no
            pre-graduation swaps are present for *mint*.
        """
        swaps = self._load_lake_swaps(mint, rows)
        result = compute_pregrad_features(swaps, deployer=deployer)
        if result is None:
            return None
        return {
            **result,
            "_feature_set_hash": self._feature_set.hash,
            "_math_version": self._feature_set.math_version,
        }

    def extract_pregrad_from_db(
        self,
        mint: str,
        *,
        deployer: str | None = None,
    ) -> dict | None:
        """Extract the 20 pre_* buyer-cohort features from pre-graduation DB rows.

        Reuses the same _load_db_swaps adapter as extract_from_db, then
        delegates to compute_pregrad_features (which selects only rel < 0 swaps).

        Args:
            mint:     Token mint address.
            deployer: Optional deployer wallet for pre_deployer_* features.

        Returns:
            Dict of 20 pre_* features with feature-set stamps, or None if no
            pre-graduation swaps exist in the DB for *mint*.
        """
        swaps = self._load_db_swaps(mint)
        result = compute_pregrad_features(swaps, deployer=deployer)
        if result is None:
            return None
        return {
            **result,
            "_feature_set_hash": self._feature_set.hash,
            "_math_version": self._feature_set.math_version,
        }

    # ------------------------------------------------------------------
    # Source adapters — produce an identical §7.1 list from each source
    # ------------------------------------------------------------------

    @staticmethod
    def _load_db_swaps(mint: str) -> list[dict]:
        """Load Swap rows for *mint* from the DB mirror, ordered by stable key."""
        from core.models import Swap

        qs = Swap.objects.filter(mint=mint).order_by("block_time", "slot", "signature")
        return [_db_row_to_micro(s) for s in qs]

    @staticmethod
    def _load_lake_swaps(mint: str, rows: list[dict]) -> list[dict]:
        """Filter lake rows by *mint*, normalize to §7.1 format, sort by stable key."""
        filtered = [r for r in rows if r.get("mint") == mint]
        filtered.sort(key=lambda r: (int(r["block_time"]), int(r["slot"]), str(r["signature"])))
        return [_lake_row_to_micro(r) for r in filtered]

    # ------------------------------------------------------------------
    # The ONE compute path (Principle #2)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute(swaps: list[dict], *, window_s: int, bucket_s: int) -> dict | None:
        """The single compute path — DB and lake paths both end here.

        AC-30.2 causal cutoff (§6.4.4): hard-rejects any swap with
        rel >= window_s BEFORE calling compute_features.  This makes the
        extractor-layer cutoff explicit and auditable — compute_features also
        enforces it internally, but the extractor is the authoritative boundary.

        The returned feature dict is stamped with ``_window_s`` so every result
        is traceable to its [0, window_s) extraction window.

        Returns:
            Feature dict (with ``_window_s`` stamp) or None (no usable swaps
            within the causal window).
        """
        # Hard-reject swaps at or beyond the causal cutoff (§6.4.4 / AC-30.2).
        windowed = [s for s in swaps if s["rel"] < window_s]
        features = compute_features(windowed, window_s=window_s, bucket_s=bucket_s)
        if features is None:
            return None
        # Stamp every feature result with the window that produced it.
        return {**features, "_window_s": window_s}
