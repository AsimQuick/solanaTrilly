# ---
# module: core.tape.birdeye_swap_mapper
# sprint: sprint-5
# story: US-22 AC-22.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: typing
# ---
"""map_birdeye_swap — raw Birdeye SUBSCRIBE_TXS event -> the internal swap dict.

The Birdeye WebSocket (and the seek_by_time REST backfill) emit swaps in
Birdeye-native shape (``blockUnixTime``, ``txHash``, ``tokenPrice``,
``volumeUSD``, ``from``/``to`` legs, ``blockNumber`` ...).  The recorder core
(TapeRecorder / NormalizedSwap.from_raw_swap, §7.1) consumes a single INTERNAL
shape (``block_time``, ``signature``, ``price``, ``vol_sol``, ``vol_usd``,
``sol_usd``, ``quote_mint`` ...).  This pure function is the one mapping between
them — it lives in the adapter layer (core/tape/), imports no concrete source
and no clock, and performs no I/O.

Mapping (PRD §3.3 / §7.1, validated byte-exact against the banked golden capture):
  blockUnixTime -> block_time      tokenPrice  -> price        (quote-implied, §3.3)
  blockNumber   -> slot            volumeUSD   -> vol_usd
  txHash        -> signature       owner       -> owner        (tx signer, §3.3)
  tokenAddress  -> mint            side        -> side
  quote leg (the from/to leg whose address != mint, normally WSOL):
      uiAmount  -> vol_sol         price       -> sol_usd      quote_mint
  base_reserve / quote_reserve = None  (§3.3: no reserve decode on the Birdeye
      live side; reserves are the helius_verify path only, §7.1).
  failed = False  (Birdeye SUBSCRIBE_TXS / the capture filter emit landed swaps).

Returns None for any event that cannot be mapped cleanly (missing legs, the
token leg not identifiable, or a non-positive/absent price or volume).  Callers
(MappedSwapSource) skip None so a malformed message never corrupts the tape.
"""
from typing import Any, Optional

#: Wrapped-SOL mint — the usual PumpSwap quote leg.  Not hardcoded into the
#: mapping (quote_mint is read from the event per §3.3 "always read quote_mint
#: first"); exported for callers that want to assert the usual case.
WSOL_MINT: str = "So11111111111111111111111111111111111111112"


def _num(value: Any) -> Optional[float]:
    """Best-effort float cast; None on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def map_birdeye_swap(raw: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map one raw Birdeye SUBSCRIBE_TXS event to the internal swap dict (§7.1).

    Returns None when the event cannot be mapped to a complete, non-degenerate
    swap (the recorder's own degenerate guard is the second line of defence).
    """
    if not isinstance(raw, dict):
        return None

    token_mint = raw.get("tokenAddress")
    from_leg = raw.get("from")
    to_leg = raw.get("to")
    if not token_mint or not isinstance(from_leg, dict) or not isinstance(to_leg, dict):
        return None

    # Identify the quote leg = the non-token side (WSOL/USDC).  §3.3: always read
    # quote_mint from the event; never hardcode WSOL.
    if from_leg.get("address") == token_mint:
        quote_leg = to_leg
    elif to_leg.get("address") == token_mint:
        quote_leg = from_leg
    else:
        return None  # token leg not identifiable

    price = _num(raw.get("tokenPrice"))
    vol_usd = _num(raw.get("volumeUSD"))
    vol_sol = _num(quote_leg.get("uiAmount"))
    # sol_usd: USD per quote unit, from the quote leg's price (fallback nearestPrice).
    sol_usd = _num(quote_leg.get("price"))
    if sol_usd is None:
        sol_usd = _num(quote_leg.get("nearestPrice"))
    block_time = raw.get("blockUnixTime")
    slot = raw.get("blockNumber")
    signature = raw.get("txHash")
    side = raw.get("side")

    if price is None or vol_usd is None or vol_sol is None or sol_usd is None:
        return None
    if block_time is None or slot is None or not signature:
        return None
    if side not in ("buy", "sell"):
        return None

    return {
        "mint": str(token_mint),
        "block_time": int(block_time),
        "slot": int(slot),
        "signature": str(signature),
        "side": str(side),
        "price": price,
        "vol_sol": abs(vol_sol),
        "vol_usd": vol_usd,
        "sol_usd": sol_usd,
        "owner": raw.get("owner"),
        "base_reserve": None,   # §3.3/§7.1: None for Birdeye (no reserve decode live)
        "quote_reserve": None,
        "quote_mint": str(quote_leg.get("address")),
        "failed": False,
    }
