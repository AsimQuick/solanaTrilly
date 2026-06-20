# ---
# module: core.tape.mint_decimals
# sprint: sprint-14
# story: US-76 AC-2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: none
# ---
"""Per-mint token decimals resolution (#288).

solanaBilly bug #288: token program was hardcoded to SPL — Token-2022 mints
have different decimals (e.g. 8 decimals vs SPL's typical 6) and the hardcoded
assumption caused silent bad conversion for every non-SPL mint.

This module provides a single, centralised per-mint decimals resolver.

Resolution order:
  1. Graduation-event seed: the MEME_DATA frame from SUBSCRIBE_MEME carries
     ``decimals`` directly — the most reliable source (from the chain, delivered
     live by Birdeye).  Thread this through as the seed when scheduling a score.
  2. Chain/account lookup stub: a ``fetch_mint_decimals`` async stub is provided
     for callers that need on-demand resolution without a graduation-event seed.
     The live implementation is a single ``getAccountInfo`` call on the mint; it
     is intentionally async so the hot path can call it once and cache.
  3. Fallback: ``DEFAULT_BASE_DECIMALS`` (6 — the pump.fun bonding-curve default).
     This is documented here as a fallback, NOT as a hardcoded truth.  Any caller
     that hardcodes 6 elsewhere is violating this contract.

For v3.2: the 20 features are within-mint ratios (vol shares, HHI, cohort
fractions) so consistent decimals per-mint are what matter — absolute magnitudes
cancel.  The resolver matters most for v4 (absolute-$ features) and for the data
contract export (solanatrills consumes raw tapes in their correct UI units).
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

#: Pump.fun bonding-curve base-token decimals (SPL program default).
#: Used as a fallback ONLY — resolve per-mint from the graduation event first.
DEFAULT_BASE_DECIMALS: int = 6

#: SOL (wSOL) quote decimals — always 9 lamports per SOL.
DEFAULT_QUOTE_DECIMALS: int = 9


class MintDecimalsResolver:
    """Centralised per-mint decimals cache.

    Holds a dict of mint → base_decimals, populated from graduation events
    (the MEME_DATA ``decimals`` field) and optionally enriched by chain lookups.

    Thread-safety: the resolver is not thread-safe.  It is designed to be
    used within a single async task (the firehose daemon) or within a single
    test.  For multi-threaded callers, create one instance per thread.

    Usage pattern::

        resolver = MintDecimalsResolver()
        # On graduation event (MEME_DATA carries decimals):
        resolver.seed(mint, decimals=6)
        # At feature-assembly time:
        base_dec = resolver.resolve(mint)   # 6
    """

    def __init__(self) -> None:
        self._cache: dict[str, int] = {}

    def seed(self, mint: str, *, decimals: int) -> None:
        """Register per-mint decimals from a graduation event or chain lookup.

        Args:
            mint:     The base-token mint address (base58).
            decimals: The token's decimal precision (e.g. 6 for pump.fun SPL,
                      8 for some Token-2022 mints, 9 for SOL).
        """
        if not isinstance(decimals, int) or decimals < 0 or decimals > 38:
            logger.warning(
                "mint_decimals.seed: invalid decimals=%r for mint=%s — ignoring",
                decimals,
                mint,
            )
            return
        self._cache[mint] = decimals
        logger.debug("mint_decimals.seed: %s -> %d decimals", mint, decimals)

    def resolve(self, mint: str) -> int:
        """Return the per-mint decimals, falling back to the default.

        Args:
            mint: The base-token mint address (base58).

        Returns:
            The resolved decimal precision for this mint.  Falls back to
            ``DEFAULT_BASE_DECIMALS`` (6) if the mint is not in the cache.
        """
        if mint in self._cache:
            return self._cache[mint]
        logger.debug(
            "mint_decimals.resolve: mint=%s not in cache — using default %d",
            mint,
            DEFAULT_BASE_DECIMALS,
        )
        return DEFAULT_BASE_DECIMALS

    def has(self, mint: str) -> bool:
        """Return True if this mint has a cached decimals value."""
        return mint in self._cache

    def known_mints(self) -> frozenset[str]:
        """Return the set of mints with seeded decimals."""
        return frozenset(self._cache)


async def fetch_mint_decimals(mint: str) -> int:
    """Async stub: fetch per-mint decimals from the chain (getAccountInfo).

    This stub returns ``DEFAULT_BASE_DECIMALS`` (6) without a network call.
    The live implementation issues a single ``getAccountInfo`` RPC against the
    mint account, reads the ``data`` field (Mint layout byte 44 = decimals for
    SPL; Token-2022 uses an extension-aware offset), and caches the result.

    Replace this stub with the real RPC call when the Helius/Solana RPC client
    is available in the firehose context.

    Args:
        mint: The base-token mint address (base58).

    Returns:
        Decimal precision for the mint (default 6 in the stub).
    """
    logger.debug(
        "fetch_mint_decimals: stub returning default %d for %s "
        "(replace with real getAccountInfo RPC for Token-2022 support)",
        DEFAULT_BASE_DECIMALS,
        mint,
    )
    return DEFAULT_BASE_DECIMALS


#: Module-level singleton resolver used by the firehose daemon.
#: Tests that need isolation should construct their own MintDecimalsResolver().
_default_resolver: MintDecimalsResolver = MintDecimalsResolver()


def get_default_resolver() -> MintDecimalsResolver:
    """Return the module-level singleton MintDecimalsResolver.

    The firehose daemon seeds this resolver from each graduation event and
    passes ``resolver.resolve(mint)`` as ``base_decimals`` to
    ``normalize_raw_for_features``.
    """
    return _default_resolver
