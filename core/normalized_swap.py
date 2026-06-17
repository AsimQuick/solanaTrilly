# ---
# module: core.normalized_swap
# sprint: sprint-8
# story: US-34 AC-34.1
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: dataclasses, typing, core.encoders
# ---
"""NormalizedSwap — the one schema the vendored feature math will eat (PRD §7.1).

This is the single canonical representation of a recorded PumpSwap swap.
All tape sources (birdeye_live, birdeye_backfill, helius_verify, helius_live) produce
NormalizedSwap instances; all downstream consumers (feature assembly, scorer,
lake writer, DB writer) read NormalizedSwap instances.

rel is ALWAYS anchored to Token.graduated_block_time from the DB row.
It is NEVER derived from the first swap's block_time.
"""
import json
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
        }

    def to_json(self) -> str:
        """Serialize to JSON using JsonSafeEncoder (keeps the H3/US-5 guard green)."""
        return json.dumps(self.to_dict(), cls=JsonSafeEncoder)
