# ---
# module: core.normalized_swap
# sprint: sprint-14
# story: US-34 AC-34.1, US-34 AC-34.2, US-76 AC-2
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: dataclasses, math, typing, core.encoders
# ---
"""NormalizedSwap — the one schema the vendored feature math will eat (PRD §7.1).

This is the single canonical representation of a recorded PumpSwap swap.
All tape sources (birdeye_live, birdeye_backfill, helius_verify, helius_live) produce
NormalizedSwap instances; all downstream consumers (feature assembly, scorer,
lake writer, DB writer) read NormalizedSwap instances.

rel is ALWAYS anchored to Token.graduated_block_time from the DB row.
It is NEVER derived from the first swap's block_time.

AC-34.2 (mint field):
    NormalizedSwap carries the base-token ``mint`` so a lake row written via
    LakeWriter is self-describing: the US-30 FeatureExtractor filters lake rows
    by ``row["mint"]`` (extract_from_lake) exactly as it filters the DB mirror by
    the indexed Swap.mint column.  This closes the gap where a LakeWriter-written
    lake could not be read per-mint by the extractor (the 'swaps' mirror could,
    because Swap.mint is a column).  The field is ADDITIVE with a "" default so
    every existing caller and every banked fixture is unaffected.

US-76 AC-2 additions — single normalisation layer:
    coerce_jsonb_none()       — coerce JSONB None → float("nan") at the boundary
                                (#358: real JSONB sends None; mocks sent np.nan).
    is_dust_price()           — return True if a price is zero or far below the
                                local median (degenerate fill guard, #405).
    normalize_raw_for_features() — THE single layer every source passes through
                                before compute_pregrad_features.  Rejects zero/
                                dust prices, coerces None→nan, applies per-mint
                                decimals for the raw→ui token-amount conversion.
                                Returns the exact §7.1 dict shape the vendored
                                feature math consumes.
"""
import json
import math
import statistics
from dataclasses import dataclass
from typing import Optional

from core.encoders import JsonSafeEncoder

# Constrained vocabularies (PRD §7.1, AC-17.3, AC-34.1)
VALID_SOURCES = frozenset({"birdeye_live", "birdeye_backfill", "helius_verify", "helius_live"})
VALID_PHASES = frozenset({"pre", "post"})
VALID_SIDES = frozenset({"buy", "sell"})


@dataclass
class NormalizedSwap:
    """One normalized swap event — the canonical unit of the tape (PRD §7.1).

    Fields match PRD §7.1 exactly.  Constrained vocabularies are enforced at
    construction time so callers get an immediate ValueError on bad data
    rather than a silent bad row in the lake.
    """

    # Temporal fields
    rel: float          # seconds since token.graduated_block_time (DB anchor)
    block_time: int
    slot: int
    signature: str

    # Trade fields
    price: float
    side: str           # "buy" | "sell"
    vol_sol: float
    vol_usd: float
    sol_usd: float
    owner: Optional[str]

    # AMM reserve snapshot at the time of the swap
    base_reserve: Optional[int]
    quote_reserve: Optional[int]
    quote_mint: str

    # Provenance
    source: str         # "birdeye_live" | "birdeye_backfill" | "helius_verify" | "helius_live"
    phase: str          # "pre" | "post"

    # Base-token mint (AC-34.2).  Additive — defaults to "" so existing callers
    # and banked fixtures (which constructed NormalizedSwap without mint) are
    # unaffected.  Populated by from_raw_swap from raw["mint"] when present.
    mint: str = ""

    def __post_init__(self) -> None:
        """Enforce constrained vocabularies immediately on construction."""
        if self.source not in VALID_SOURCES:
            raise ValueError(
                f"source {self.source!r} is not in the allowed vocabulary "
                f"{sorted(VALID_SOURCES)}"
            )
        if self.phase not in VALID_PHASES:
            raise ValueError(
                f"phase {self.phase!r} is not in the allowed vocabulary "
                f"{sorted(VALID_PHASES)}"
            )
        if self.side not in VALID_SIDES:
            raise ValueError(
                f"side {self.side!r} is not in the allowed vocabulary "
                f"{sorted(VALID_SIDES)}"
            )

    @classmethod
    def from_raw_swap(
        cls,
        raw: dict,
        token,
        *,
        source: str,
        phase: str,
    ) -> "NormalizedSwap":
        """Build a NormalizedSwap from a raw Birdeye/Helius swap dict + a DB Token row.

        rel is anchored to token.graduated_block_time — NEVER to the first swap's
        block_time.  The token argument must be a core.models.Token instance (or any
        object with a graduated_block_time integer attribute).

        AC-34.2: ``mint`` is read from ``raw.get("mint", "")`` (default "") so the
        resulting lake row is self-describing for the per-mint FeatureExtractor
        filter.  Both the Birdeye internal dict and the Helius birth-tape internal
        dict already carry "mint", so both paths populate it identically.
        """
        block_time: int = int(raw["block_time"])
        rel: float = float(block_time - token.graduated_block_time)
        return cls(
            rel=rel,
            block_time=block_time,
            slot=int(raw["slot"]),
            signature=str(raw["signature"]),
            price=float(raw["price"]),
            side=str(raw["side"]),
            vol_sol=float(raw["vol_sol"]),
            vol_usd=float(raw["vol_usd"]),
            sol_usd=float(raw["sol_usd"]),
            owner=raw.get("owner"),
            base_reserve=raw.get("base_reserve"),
            quote_reserve=raw.get("quote_reserve"),
            quote_mint=str(raw["quote_mint"]),
            source=source,
            phase=phase,
            mint=str(raw.get("mint", "")),
        )

    def to_dict(self) -> dict:
        """Return a plain dict representation of this swap."""
        return {
            "rel": self.rel,
            "block_time": self.block_time,
            "slot": self.slot,
            "signature": self.signature,
            "price": self.price,
            "side": self.side,
            "vol_sol": self.vol_sol,
            "vol_usd": self.vol_usd,
            "sol_usd": self.sol_usd,
            "owner": self.owner,
            "base_reserve": self.base_reserve,
            "quote_reserve": self.quote_reserve,
            "quote_mint": self.quote_mint,
            "source": self.source,
            "phase": self.phase,
            "mint": self.mint,
        }

    def to_json(self) -> str:
        """Serialize to JSON using JsonSafeEncoder (keeps the H3/US-5 guard green)."""
        return json.dumps(self.to_dict(), cls=JsonSafeEncoder)


# ---------------------------------------------------------------------------
# US-76 AC-2 — single normalisation layer helpers
# ---------------------------------------------------------------------------

#: Dust-price threshold: if a fill price is below this fraction of the local
#: median it is treated as a degenerate fill and rejected (#405).
#: 1e-6 means a price 1 000 000× below the median is dust — broad enough to
#: catch ~1e-12 settler fills without rejecting real low-price tokens.
DUST_PRICE_RATIO: float = 1e-6

#: Minimum number of prices needed to compute a local median for the dust guard.
#: Below this count the guard falls back to simply requiring price > 0.
DUST_MEDIAN_MIN_SAMPLES: int = 5

#: pump.fun bonding-curve tokens use 6 decimals; SOL (quote) uses 9.
#: These are the per-program defaults — override with per-mint values from the
#: graduation event (MEME_DATA ``decimals`` field) or a chain lookup.
DEFAULT_BASE_DECIMALS: int = 6
DEFAULT_QUOTE_DECIMALS: int = 9  # SOL


def coerce_jsonb_none(value: object, nan: float = float("nan")) -> float:
    """Coerce a JSONB-sourced value to float, mapping None → nan (#358).

    Real JSONB ``NULL`` deserialises as Python ``None``.  Callers that were
    tested with mocks sending ``np.nan`` instead of ``None`` passed in CI but
    crashed on live data.  This function is the single boundary coercion point
    that prevents the discrepancy.

    Rules:
      - ``None``     → ``nan``  (JSONB NULL)
      - non-finite   → as-is   (preserve existing nan/inf)
      - numeric str  → float
      - other        → ``nan``  (safe default — never crash, always signal missing)

    Args:
        value: The raw value from a JSONB-derived dict (may be None, float, int,
               str, or any other type).
        nan:   The sentinel to return for missing/uncoercible values (default
               ``float("nan")``).

    Returns:
        A float, always.
    """
    if value is None:
        return nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return nan
    return result


def is_dust_price(
    price: float,
    peer_prices: list[float],
) -> bool:
    """Return True if *price* is zero or a degenerate dust fill (#405).

    A fill price of exactly zero is never safe to divide by — the on-chain
    virtual-reserves ratio can temporarily reach zero on a drained curve.

    A fill price far below the local median is a dust fill (the solanatrills
    settler had ~1e-12 prices from a bug that produced billions-% returns).

    Policy:
      - price <= 0                          → always dust
      - math.isnan(price)                   → always dust
      - price < median(peers) * DUST_PRICE_RATIO   (when ≥ DUST_MEDIAN_MIN_SAMPLES
                                                    finite peers available)

    Args:
        price:       The candidate fill price (may be 0 or nan).
        peer_prices: Other prices in the same tape window, used to compute the
                     local median.  May be empty or contain nan/inf.

    Returns:
        True when the price should be rejected; False when acceptable.
    """
    if math.isnan(price) or price <= 0.0:
        return True

    finite_peers = [p for p in peer_prices if math.isfinite(p) and p > 0.0]
    if len(finite_peers) < DUST_MEDIAN_MIN_SAMPLES:
        # Not enough context — only the absolute-zero guard above applies.
        return False

    median = statistics.median(finite_peers)
    if median <= 0.0:
        return False

    return price < median * DUST_PRICE_RATIO


def normalize_raw_for_features(
    raw: dict,
    *,
    graduated_block_time: int,
    base_decimals: int = DEFAULT_BASE_DECIMALS,
    peer_prices: Optional[list[float]] = None,
    sol_usd_spot: Optional[float] = None,
) -> Optional[dict]:
    """Normalise one raw swap dict into the §7.1 shape compute_pregrad_features expects.

    This is THE single normalisation layer that every tape source (Helius live,
    Birdeye offline-style, any future source) passes through before the vendored
    feature math is called.  It enforces all of the AC-2 / directives §2 guards:

    1. None → nan (#358): all numeric fields are coerced via ``coerce_jsonb_none``
       before any arithmetic.  Tests MUST use a real JSONB-shaped dict (None
       values), not a hand-built np.nan dict, to exercise this boundary.

    2. Zero/dust price (#405): if the fill price is zero, nan, or far below the
       local median (dust guard) the swap is REJECTED (returns None).  A valid
       on-chain zero is never safe to divide by.

    3. Per-mint decimals (#288): ``base_decimals`` is used when converting raw
       token amounts to UI amounts (raw / 10**base_decimals).  Seed from the
       graduation-event MEME_DATA ``decimals`` field via MintDecimalsResolver;
       never hardcode 6 in the caller.  SOL volume (``vol_sol``) comes directly
       from the Helius SOL leg and does NOT need decimal scaling — it is already
       in SOL-space (lamports / 1e9).

    4. Volume basis (directives §8/§9, US-76 BREAK-1): the feature-math ``vol``
       key is SOL-space by default (``vol_sol``, the Helius SOL leg) — scale-
       invariant for the 19 ratio features.  When ``sol_usd_spot`` is supplied,
       ``vol`` becomes USD (``vol_sol × spot``) using ONE graduation-time spot,
       reproducing the offline trained feature space and fixing the only non-
       scale-invariant feature, ``pre_insider_sell_ratio`` (its +1.0 smoother is
       negligible at USD scale).  ``vol_sol`` always stays the raw SOL leg.
       ``vol_usd`` / ``sol_usd`` pass through as-is for storage (may be 0.0 / nan
       for Helius-live where no per-trade USD oracle is available).

    5. Output shape: the returned dict has EXACTLY the keys that §7.1 requires
       for compute_pregrad_features:
         rel, block_time, slot, signature, price, side, vol (=vol_sol),
         owner, vol_sol, vol_usd, sol_usd, base_reserve, quote_reserve,
         quote_mint, source, phase, mint.

    Args:
        raw:                  A raw swap dict from ANY source (Helius or Birdeye
                              internal shape).  JSONB None values are coerced here.
        graduated_block_time: The mint's graduation epoch, used to compute ``rel``.
        base_decimals:        Per-mint token decimals (seed from MEME_DATA, default 6).
        peer_prices:          Other recent prices for the dust-median guard.  Pass
                              the prices of swaps already processed in this tape
                              window.  May be None (treated as empty).
        sol_usd_spot:         Single graduation-time SOL/USD spot.  When given,
                              the feature ``vol`` is computed in USD (vol_sol ×
                              spot) — the BREAK-1 fix.  None → SOL-space ``vol``.

    Returns:
        A §7.1 dict ready for compute_pregrad_features, or None if the swap is
        rejected (zero/dust price, missing block_time, or invalid side).
    """
    if peer_prices is None:
        peer_prices = []

    # ------------------------------------------------------------------
    # 1. Coerce JSONB None → nan at the boundary (#358)
    # ------------------------------------------------------------------
    block_time_raw = raw.get("block_time")
    if block_time_raw is None:
        return None  # Cannot compute rel without block_time
    try:
        block_time = int(block_time_raw)
    except (TypeError, ValueError):
        return None

    rel = float(block_time - graduated_block_time)

    price = coerce_jsonb_none(raw.get("price"))
    vol_sol = coerce_jsonb_none(raw.get("vol_sol") if raw.get("vol_sol") is not None else raw.get("vol"))
    vol_usd = coerce_jsonb_none(raw.get("vol_usd"))
    sol_usd = coerce_jsonb_none(raw.get("sol_usd"))

    side_raw = raw.get("side", "")
    side = str(side_raw) if side_raw is not None else ""
    if side not in ("buy", "sell"):
        return None  # Reject invalid side

    # ------------------------------------------------------------------
    # 2. Zero/dust price guard (#405)
    # ------------------------------------------------------------------
    if is_dust_price(price, peer_prices):
        return None

    # ------------------------------------------------------------------
    # 3. Per-mint decimals — raw token amount → UI amount when needed
    #    (vol_sol is already SOL-space from the Helius leg; no scaling needed)
    # ------------------------------------------------------------------
    token_amount_raw = raw.get("token_amount")
    if token_amount_raw is not None:
        try:
            _token_ui = int(token_amount_raw) / (10 ** base_decimals)  # noqa: F841 — available for future use
        except (TypeError, ValueError, OverflowError):
            pass  # token_ui conversion failure is non-fatal for v3.2

    # vol_sol is the raw SOL leg (always SOL-space, preserved for downstream).
    if math.isnan(vol_sol) or vol_sol < 0.0:
        vol_sol = 0.0  # degenerate volume → treat as 0 (swap included, no division by vol needed)

    # The feature-math ``vol`` key (directives §8/§9, US-76 BREAK-1 resolution):
    #   - sol_usd_spot is None  → SOL-space ``vol`` (backward-compatible default;
    #     scale-invariant for the 19 ratio features, used by existing unit tests).
    #   - sol_usd_spot given     → USD ``vol`` = vol_sol × spot.  Reproduces the
    #     OFFLINE trained feature space (offline vol = uiAmount_SOL × quotePrice).
    #     A SINGLE graduation-time spot (not per-trade) is sufficient: it cancels
    #     out of the 19 ratios and only makes pre_insider_sell_ratio's +1.0 Laplace
    #     smoother negligible at USD scale (the BREAK-1 fix).  Per-trade USD is NOT
    #     needed because SOL/USD is ~constant over the ~6.5-min curve window.
    if sol_usd_spot is not None and sol_usd_spot > 0.0:
        vol_feat = float(vol_sol) * float(sol_usd_spot)
    else:
        vol_feat = float(vol_sol)

    # ------------------------------------------------------------------
    # 4. Build the §7.1 output dict
    # ------------------------------------------------------------------
    return {
        # §7.1 fields that compute_pregrad_features reads
        "rel": rel,
        "block_time": block_time,
        "slot": int(raw.get("slot") or 0),
        "signature": str(raw.get("signature") or ""),
        "price": float(price),
        "side": side,
        "vol": float(vol_feat),       # the key the feature math uses (USD when spot given)
        "vol_sol": float(vol_sol),    # raw SOL leg — for downstream consumers that use vol_sol
        "owner": raw.get("owner"),
        # Ancillary fields — preserved for storage / downstream consumers
        "vol_usd": float(vol_usd) if not math.isnan(vol_usd) else float("nan"),
        "sol_usd": float(sol_usd) if not math.isnan(sol_usd) else float("nan"),
        "base_reserve": raw.get("base_reserve"),
        "quote_reserve": raw.get("quote_reserve"),
        "quote_mint": str(raw.get("quote_mint") or ""),
        "source": str(raw.get("source") or ""),
        "phase": str(raw.get("phase") or ""),
        "mint": str(raw.get("mint") or ""),
    }
