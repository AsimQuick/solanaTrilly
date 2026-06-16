# ---
# module: core.snapshot_schema
# sprint: sprint-6
# story: US-23 AC-23.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: dataclasses, typing, json, core.encoders
# ---
"""Score-time snapshot schema — the ONE typed shape between the fetcher and feature assembly.

SnapshotSchema is a dataclass capturing the seven fields that the single on-demand
Birdeye REST read (US-24) provides at score time (PRD §1.1, §6.3):

  holder_distribution  Holder breakdown (count, top-N %) — from Birdeye holders API.
  mint_authority       Mint authority address; None when renounced.
  freeze_authority     Freeze authority address; None when renounced.
  lp_burned            True when LP tokens have been verifiably burned.
  liquidity            Pool liquidity in USD (§1.1 — first-class, never assumed).
  tvl                  Total value locked in USD (§1.1 — first-class, never assumed).
  depth                Bid/ask market depth dict (§1.1 — first-class, never assumed).

liquidity, tvl, and depth are required in every raw payload — they are NEVER
defaulted or assumed (Principle §1.1).  KeyError is raised if absent.

JSON serialisation via to_json_safe() uses JsonSafeEncoder so the H3/US-5 guard
stays green (NaN/Inf → null, Decimal → float, datetime → ISO string).
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import Any, Optional

from core.encoders import JsonSafeEncoder


@dataclass
class SnapshotSchema:
    """Typed score-time snapshot schema (PRD §1.1, §6.3).

    The ONE shape the fetcher (US-24) emits and feature assembly (US-27+) consumes.
    All seven fields are mandatory; liquidity / tvl / depth are first-class and
    are never defaulted or assumed.
    """

    holder_distribution: Any
    mint_authority: Optional[str]
    freeze_authority: Optional[str]
    lp_burned: bool
    liquidity: float
    tvl: float
    depth: dict

    @classmethod
    def from_raw(cls, raw: dict) -> "SnapshotSchema":
        """Construct from a raw Birdeye snapshot payload dict (Snapshot.raw JSONB).

        All seven keys must be present in raw.  liquidity / tvl / depth are
        extracted verbatim — they are never defaulted to 0 or any assumed value
        (§1.1).  KeyError is raised if any required key is absent so the caller
        sees a clear failure rather than a silently wrong schema.

        mint_authority and freeze_authority use .get() — both may be None (null)
        when the authority is renounced, which is a valid and expected state.
        """
        return cls(
            holder_distribution=raw["holder_distribution"],
            mint_authority=raw.get("mint_authority"),
            freeze_authority=raw.get("freeze_authority"),
            lp_burned=bool(raw["lp_burned"]),
            liquidity=raw["liquidity"],
            tvl=raw["tvl"],
            depth=raw["depth"],
        )

    def to_json_safe(self) -> str:
        """Serialise to JSON using JsonSafeEncoder (H3/US-5 guard).

        Converts non-finite floats to null, Decimal to float, and datetime to
        ISO string before encoding — the same guarantees the Snapshot.raw
        JSONField provides at the persistence layer.
        """
        return json.dumps(dataclasses.asdict(self), cls=JsonSafeEncoder)
