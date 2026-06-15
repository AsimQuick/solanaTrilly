# ---
# module: core.tape.swap_writer
# sprint: sprint-5
# story: US-19 AC-19.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.normalized_swap, core.models, typing
# ---
"""SwapWriter — idempotent mirror of the jsonl.gz lake to the 'swaps' DB table (PRD §6.4).

Writes NormalizedSwaps to the Swap Django model (table 'swaps') using
update_or_create keyed on (mint, signature) so that re-recording the same swap
never creates a duplicate row (AC-19.2).

The Swap table is the *queryable* mirror of the immutable jsonl.gz lake.
The lake is the raw truth (§6.4.1); the Swap table is derived and idempotent.
"""
from typing import Optional

from core.models import Swap
from core.normalized_swap import NormalizedSwap


class SwapWriter:
    """Writes NormalizedSwap instances to the 'swaps' DB table, idempotent on (mint, signature).

    Usage::

        writer = SwapWriter()
        writer.write([("MINT_ABC...", normalized_swap), ...])

    Each call to write() is safe to repeat — re-writing the same (mint, signature)
    pair updates the existing row rather than inserting a duplicate.
    """

    def write(self, swaps: list[tuple[str, NormalizedSwap]]) -> int:
        """Upsert each swap into the 'swaps' table, idempotent on (mint, signature).

        Args:
            swaps: List of (mint, NormalizedSwap) pairs.  mint is the base-token
                   address (from the detection/token_store side); signature is the
                   on-chain transaction signature.

        Returns:
            Number of rows processed (created + updated combined).
        """
        count = 0
        for mint, swap in swaps:
            Swap.objects.update_or_create(
                mint=mint,
                signature=swap.signature,
                defaults={
                    "block_time": swap.block_time,
                    "slot": swap.slot,
                    "side": swap.side,
                    "price": swap.price,
                    "vol_sol": swap.vol_sol,
                    "vol_usd": swap.vol_usd,
                    "sol_usd": swap.sol_usd,
                    "owner": swap.owner,
                    "base_reserve": swap.base_reserve,
                    "quote_reserve": swap.quote_reserve,
                    "rel": swap.rel,
                },
            )
            count += 1
        return count

    def write_empty(self) -> Optional[int]:
        """Write an empty batch — returns None (mirrors LakeWriter.write([]) semantics)."""
        return None
