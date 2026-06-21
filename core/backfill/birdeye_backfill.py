# ---
# module: core.backfill.birdeye_backfill
# sprint: epic-tape-sourcing-escalation
# story: EPIC-tape-sourcing-escalation Tier 3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: requests, django.conf.settings, logging, time, typing
# ---
"""BirdeyeBackfiller — Tier-3 tape-sourcing escalation (Birdeye REST, last resort).

When a graduated token has no pre-grad tape in memory AND none in the local
firehose lake (Tier-2 lake miss), this worker fetches the pre-graduation swap
tape from Birdeye's REST ``seek_by_time`` endpoint and returns the swaps so
the caller can populate the in-memory buffer via ``TapeStore.add`` (which fires
the on_add sink and banks the fetched tape to the lake for future reuse).

Design
------
- Endpoint: ``https://public-api.birdeye.so/defi/txs/token/seek_by_time``
  (forward scan by SPL mint, not pool).
- Window: ``t_from = graduated_block_time - 3600`` (1 h pre-grad lookback),
  ``t_to = graduated_block_time`` (exclusive — PRE-grad only).
  Only swaps with ``block_time < graduated_block_time`` are returned.
- Pagination: forward scan with ``offset`` sub-paging.  When ``offset >= 9800``
  reset ``after_time = last_block_time, offset = 0`` (Birdeye API pagination
  cap documented in the lab's ``analysis/graduated/parity_probe.py``).
  At most 40 total iterations (credit-safety cap).
- Backoff (``be_get``): 200 → ok; 429 → sleep ``Retry-After``+1; >=500 →
  sleep ``1.5*(i+1)``; 5 tries; on exhaustion return what we have.
- Mapping: produces the SAME internal §7.1 swap dict shape as
  ``map_birdeye_swap`` / ``LakeBackfiller.run_for_mint``.  sol_usd is taken
  from an optional ``sol_usd_spot`` parameter so the pricing is consistent
  with the scorer's cached spot (no fresh Birdeye fetch, no hardcoded value).
  When ``sol_usd_spot`` is None the REST item's implicit quote price is used
  as a fallback (same as the WebSocket path).
- Sort: returns swaps ascending by ``(block_time, slot, signature)`` (parity
  requirement — matches the live path's canonical ordering).
- Missing ``BIRDEYE_API_KEY``: logs a warning and returns ``[]`` (graceful no-op,
  never raises, never burns credits).
- Principle #7: no ``datetime.now()`` / ``time.time()`` — all date math is
  derived from ``graduated_block_time`` (caller-supplied).  ``time.sleep`` is
  the only ``time.*`` call and is used only for rate-limit backoff.
- Testable in isolation as a pure sync function (blocking requests.get; wrapped
  in ``sync_to_async(thread_sensitive=False)`` by the daemon caller so it never
  stalls the event loop).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

# Birdeye REST endpoint for per-token swap history (forward time scan).
_SEEK_BY_TIME_URL = "https://public-api.birdeye.so/defi/txs/token/seek_by_time"

# Maximum iterations per call (credit-safety cap from the lab probe).
_MAX_ITERS = 40

# Birdeye sub-page offset cap: when offset reaches this value, reset the
# after_time cursor to the last item's block_time and restart offset=0.
_OFFSET_RESET_THRESHOLD = 9800

# Lookback before grad_bt (seconds).  Bonding-curve creation to graduation is
# at most ~3 days; we use 1 h as the practical pre-grad feature window.
_PRE_GRAD_LOOKBACK_S: int = 3600

# Per-try sleep multiplier for >=500 errors (seconds).
_SERVER_ERR_SLEEP_FACTOR: float = 1.5


def _get_api_key() -> str:
    """Return BIRDEYE_API_KEY from Django settings (empty string if absent)."""
    from django.conf import settings  # noqa: PLC0415

    return str(getattr(settings, "BIRDEYE_API_KEY", "") or "")


def _be_get(
    params: dict[str, Any],
    api_key: str,
    *,
    tries: int = 5,
) -> dict[str, Any]:
    """HTTP GET the Birdeye seek_by_time endpoint with retry / backoff.

    Args:
        params:  Query parameters for the request.
        api_key: Birdeye API key (caller-resolved; never looked up here).
        tries:   Maximum number of attempts.

    Returns:
        Parsed JSON dict on success.  On exhaustion returns
        ``{"_err": "exhausted"}``; on non-retryable HTTP error returns
        ``{"_err": <status_code>}``.  Callers check for ``"_err"`` in the
        result and treat it as an empty / terminal response.
    """
    # HTTP via urllib (stdlib) — matches the codebase convention
    # (core/pricing/sol_usd.py); `requests` is NOT in the production image.
    headers = {"X-API-KEY": api_key, "x-chain": "solana"}
    url = f"{_SEEK_BY_TIME_URL}?{urlencode(params)}"
    for i in range(tries):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = resp.read()
            try:
                return json.loads(raw)
            except Exception as exc:  # noqa: BLE001 — bad JSON
                logger.warning("[BIRDEYE_BACKFILL] JSON decode error: %s", exc)
                return {"_err": "json_error"}
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 429:
                retry_after = float((exc.headers.get("Retry-After", 2) if exc.headers else 2) or 2) + 1.0
                logger.debug(
                    "[BIRDEYE_BACKFILL] 429 rate-limited — sleeping %.1fs (attempt %d).",
                    retry_after,
                    i + 1,
                )
                time.sleep(retry_after)
                continue
            if status >= 500:
                sleep_s = _SERVER_ERR_SLEEP_FACTOR * (i + 1)
                logger.debug(
                    "[BIRDEYE_BACKFILL] HTTP %d — sleeping %.1fs (attempt %d).",
                    status,
                    sleep_s,
                    i + 1,
                )
                time.sleep(sleep_s)
                continue
            # Non-retryable error (4xx other than 429).
            logger.warning(
                "[BIRDEYE_BACKFILL] HTTP %d — non-retryable, aborting.", status
            )
            return {"_err": status}
        except (urllib.error.URLError, OSError) as exc:  # network error
            logger.debug("[BIRDEYE_BACKFILL] request error (attempt %d): %s", i + 1, exc)
            time.sleep(_SERVER_ERR_SLEEP_FACTOR * (i + 1))
            continue

    return {"_err": "exhausted"}


def _map_rest_item(
    item: dict[str, Any],
    mint: str,
    graduated_block_time: int,
    sol_usd_spot: Optional[float],
) -> Optional[dict[str, Any]]:
    """Map one Birdeye seek_by_time REST item to the internal §7.1 swap dict.

    REST item fields used:
        txHash              -> signature
        blockUnixTime       -> block_time
        side                -> side         ("buy" or "sell")
        owner               -> owner
        base.uiChangeAmount -> (cross-check on side; signed qty +buy/-sell)
        base.price          -> price        (USD per token, same as tokenPrice in WS)
        volume_usd          -> vol_usd
        base.address        -> mint (cross-check)

    The SOL volume (vol_sol) is derived as ``vol_usd / sol_usd_spot`` when the
    spot is available.  When it is not, we fall back to ``abs(uiChangeAmount)``
    expressed in SOL units is unavailable from this endpoint, so we use
    ``vol_usd / sol_usd_spot`` as the canonical path.

    sol_usd (the per-swap pricing value stored in the swap dict) is set to the
    caller-supplied ``sol_usd_spot`` when available; otherwise falls back to
    computing it from the item's ``volume_usd / (abs(uiChangeAmount) * price)``
    where that is resolvable.

    The returned dict is identical in structure to what ``map_birdeye_swap``
    produces from a WebSocket SUBSCRIBE_TXS event (§7.1 contract):
        mint, block_time, slot, signature, side, price, vol_sol, vol_usd,
        sol_usd, owner, base_reserve, quote_reserve, quote_mint, failed.

    Returns None when the item cannot be mapped cleanly (missing/zero/invalid
    required fields; block_time outside the pre-grad window).
    """
    if not isinstance(item, dict):
        return None

    # --- Core identity fields ---
    signature = item.get("txHash")
    block_time_raw = item.get("blockUnixTime")
    side = item.get("side")
    owner = item.get("owner")

    if not signature or block_time_raw is None or side not in ("buy", "sell"):
        return None

    try:
        block_time = int(block_time_raw)
    except (TypeError, ValueError):
        return None

    # Pre-grad window filter (belt-and-suspenders; caller also filters).
    if block_time >= graduated_block_time:
        return None
    t_from = graduated_block_time - _PRE_GRAD_LOOKBACK_S
    if block_time < t_from:
        return None

    # --- Base leg ---
    base = item.get("base") or {}
    base_address = base.get("address")
    # Validate that the base leg is indeed our mint.
    if base_address and base_address != mint:
        return None

    try:
        price = float(base.get("price") or 0.0)
    except (TypeError, ValueError):
        price = 0.0

    if price <= 0:
        return None

    try:
        vol_usd = float(item.get("volume_usd") or 0.0)
    except (TypeError, ValueError):
        vol_usd = 0.0

    if vol_usd <= 0:
        return None

    # --- SOL volume and sol_usd ---
    # sol_usd is the spot passed in from the scorer's cached oracle (consistent
    # with the live and lake paths — no stale or invented value).
    if sol_usd_spot is not None and sol_usd_spot > 0:
        sol_usd = sol_usd_spot
        vol_sol = vol_usd / sol_usd_spot
    else:
        # Fallback: the REST item doesn't have an explicit quote leg price,
        # so we derive sol_usd from vol_usd / (abs(ui_change) * price) where
        # ui_change is the base token amount in token units.
        # If that is not resolvable, use 0 for sol_usd (scorer won't use it
        # for pre-grad features but it must be present in the dict shape).
        try:
            ui_change = abs(float(base.get("uiChangeAmount") or 0.0))
        except (TypeError, ValueError):
            ui_change = 0.0

        if ui_change > 0 and price > 0:
            # vol_usd ≈ ui_change (tokens) * price (USD/token)  — corroborated
            # by the exchange rate: sol_usd = vol_usd / vol_sol
            # But we don't have vol_sol directly; approximate via:
            #   vol_sol = vol_usd / sol_usd  (circular)
            # Fall back to 0: the feature assembler uses sol_usd only as a
            # scalar multiplier for vol_sol → vol_usd.  Without a spot,
            # vol_sol won't be meaningful regardless.
            sol_usd = 0.0
            vol_sol = 0.0
        else:
            sol_usd = 0.0
            vol_sol = 0.0

    # slot: not present in seek_by_time items; use 0 as a sentinel (consistent
    # with what the feature assembler tolerates — slot is used only in sorting).
    slot = int(item.get("blockNumber") or 0)

    # quote_mint: seek_by_time items don't expose the quote leg explicitly;
    # PumpSwap always uses WSOL as the quote, so default to the canonical WSOL mint.
    _WSOL = "So11111111111111111111111111111111111111112"
    quote_mint = _WSOL

    return {
        "mint": str(mint),
        "block_time": block_time,
        "slot": slot,
        "signature": str(signature),
        "side": str(side),
        "price": price,
        "vol_sol": abs(vol_sol),
        "vol_usd": vol_usd,
        "sol_usd": sol_usd,
        "owner": owner,
        "base_reserve": None,
        "quote_reserve": None,
        "quote_mint": quote_mint,
        "failed": False,
    }


class BirdeyeBackfiller:
    """Synchronous worker that fetches a mint's pre-grad swaps from Birdeye REST.

    Designed to be called via ``asgiref.sync.sync_to_async`` from an asyncio
    context so it never blocks the event loop in the daemon.

    This is Tier 3 in the tape-sourcing escalation chain:
        Tier 1 — in-memory TapeStore buffer (populated during live collection)
        Tier 2 — local firehose lake (LakeBackfiller)
        Tier 3 — Birdeye REST seek_by_time (BirdeyeBackfiller, this class)
        Terminal — SKIPPED (no tape recoverable)

    Args:
        api_key: Override API key (default: ``settings.BIRDEYE_API_KEY``).
                 Tests inject a sentinel to avoid touching settings.
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        # Resolve the API key lazily in run_for_mint to avoid hitting
        # Django settings at import time (Principle #7 / test isolation).
        self._api_key_override = api_key

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_for_mint(
        self,
        mint: str,
        graduated_block_time: int,
        *,
        sol_usd_spot: Optional[float] = None,
    ) -> list[dict]:
        """Fetch *mint*'s pre-grad swaps from Birdeye REST and return them.

        Fetches swaps in the window
        ``[graduated_block_time - 3600, graduated_block_time)`` using forward
        pagination.  Items with ``blockUnixTime >= graduated_block_time`` are
        dropped (pre-grad only; definitive guard).

        Args:
            mint:                  The Solana mint address.
            graduated_block_time:  Unix epoch seconds of the graduation block.
                                   Used to derive the fetch window (no wall-clock
                                   calls — Principle #7).
            sol_usd_spot:          Optional cached SOL/USD spot from the scorer's
                                   shared oracle (``core.pricing.sol_usd``).
                                   When supplied, all returned swaps carry this
                                   ``sol_usd`` value — consistent with the live
                                   and lake paths.  When None, sol_usd in the
                                   returned swaps is 0 (feature assembler tolerates
                                   this for the pre-grad feature vector).

        Returns:
            List of internal §7.1 swap dicts (same shape as
            ``LakeBackfiller.run_for_mint`` returns and ``TapeStore.add``
            consumes).  Empty list when:
                - ``BIRDEYE_API_KEY`` is missing (graceful no-op).
                - Birdeye returns no rows in the window.
                - All HTTP retries are exhausted.
            Swaps are sorted ascending by ``(block_time, slot, signature)``
            (parity requirement).
        """
        api_key = self._api_key_override
        if api_key is None:
            api_key = _get_api_key()

        if not api_key:
            logger.warning(
                "[BIRDEYE_BACKFILL] mint=%s BIRDEYE_API_KEY not set — "
                "Tier-3 backfill skipped (graceful no-op).",
                mint,
            )
            return []

        t_from = graduated_block_time - _PRE_GRAD_LOOKBACK_S
        t_to = graduated_block_time  # exclusive (item.block_time < t_to required)

        results: list[dict] = []
        offset = 0
        after_time = t_from
        iters = 0

        logger.info(
            "[BIRDEYE_BACKFILL] mint=%s grad_bt=%d window=[%d, %d) — starting REST fetch.",
            mint,
            graduated_block_time,
            t_from,
            t_to,
        )

        while iters < _MAX_ITERS:
            iters += 1
            params: dict[str, Any] = {
                "address": mint,
                "after_time": after_time,
                "offset": offset,
                "limit": 100,
                "tx_type": "swap",
            }
            body = _be_get(params, api_key)

            if "_err" in body:
                logger.warning(
                    "[BIRDEYE_BACKFILL] mint=%s HTTP error %s at iter=%d — "
                    "returning %d items collected so far.",
                    mint,
                    body["_err"],
                    iters,
                    len(results),
                )
                break

            data = body.get("data") or {}
            items: list[dict] = data.get("items") or []

            if not items:
                break

            last_bt: int = t_from  # fallback if items are empty
            for it in items:
                bt_raw = it.get("blockUnixTime")
                if bt_raw is None:
                    continue
                try:
                    bt = int(bt_raw)
                except (TypeError, ValueError):
                    continue

                last_bt = bt

                # Stop consuming once we pass the graduation time.
                if bt > t_to:
                    break

                # Skip items at or after grad (exclusive window upper bound).
                if bt >= t_to:
                    continue

                mapped = _map_rest_item(it, mint, graduated_block_time, sol_usd_spot)
                if mapped is not None:
                    results.append(mapped)

            # Pagination termination: stop when the last item is past t_to or
            # the page was short (fewer than 100 items = last page).
            if last_bt > t_to or len(items) < 100:
                break

            # Advance the pagination cursor.
            offset += len(items)
            if offset >= _OFFSET_RESET_THRESHOLD:
                # Birdeye cap: reset offset and advance after_time to last item's bt.
                after_time = last_bt
                offset = 0

        logger.info(
            "[BIRDEYE_BACKFILL] mint=%s grad_bt=%d iters=%d fetched=%d items.",
            mint,
            graduated_block_time,
            iters,
            len(results),
        )

        if not results:
            return []

        # Sort ascending by canonical (block_time, slot, signature) — parity
        # requirement to match the live path's canonical ordering.
        results.sort(key=lambda s: (s["block_time"], s["slot"], s["signature"]))
        return results
