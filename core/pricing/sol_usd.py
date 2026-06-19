# ---
# module: core.pricing.sol_usd
# sprint: cutover (copy-trade live)
# story: shared-sol-usd-spot
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: django.conf.settings, django.core.cache, urllib
# ---
"""Cached SOL/USD spot — one shared USD oracle for both trading heads.

WHY THIS EXISTS
===============
The prediction paper path inlined ``sol_usd = 140.0`` (run_firehose
``_build_scoring_context_sync``) — a latent staleness bug: SOL drifts far
enough over time that USD-denominated paper sizing silently skews.  The
copy-trade engine ALSO needs a USD value, for the cohort's
``min_trigger_buy_usd`` (>= $250) conviction gate, computed from a swap's
on-chain SOL amount.  Rather than two oracles (or two hardcodes), both heads
read this one cached spot.

DESIGN
======
- ``get_sol_usd()`` returns a cached SOL/USD price, refreshed at most once per
  ``CACHE_TTL_S`` (default 180 s) from a cheap Birdeye REST price lookup.  SOL
  barely moves minute-to-minute, so a per-trigger lookup would be wasted spend
  (the operator is sensitive to Birdeye burn) — we cache and share instead.
- Everything is INJECTED (``fetcher``, ``cache``, ``default``) so the resolver
  is exercised offline with zero network and zero Django cache dependency.
- FAIL-SAFE: any fetch failure (no API key, network error, malformed payload)
  falls back to ``default`` (140.0) and does NOT cache the fallback, so the
  next call retries the live fetch.  The pipeline never breaks on a price miss.
- NO ``datetime.now()`` / ``time.time()`` in this module — TTL expiry is handled
  internally by the cache backend, honoring the core/ injected-clock discipline.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from django.conf import settings
from django.core.cache import cache as _django_cache

#: Wrapped-SOL mint — the quote asset priced against USD.
WSOL_MINT: str = "So11111111111111111111111111111111111111112"

#: Conservative fallback when no live spot is resolvable (the former hardcode).
DEFAULT_SOL_USD: float = 140.0

#: Cache key + TTL.  180 s keeps spend negligible while staying fresh enough for
#: USD sizing and a $250 conviction gate (both tolerant to minute-scale drift).
CACHE_KEY: str = "core.pricing.sol_usd_spot"
CACHE_TTL_S: int = 180


def fetch_sol_usd_birdeye(timeout_s: float = 8.0) -> Optional[float]:
    """Fetch the current SOL/USD price from the Birdeye REST price endpoint.

    Returns the price as a float, or ``None`` when ``BIRDEYE_API_KEY`` is unset,
    the request fails, or the payload is missing/zero/negative.  Never raises —
    callers treat ``None`` as "use the cached/last/default value".
    """
    api_key: str = getattr(settings, "BIRDEYE_API_KEY", "")
    if not api_key:
        return None

    url = f"https://public-api.birdeye.so/defi/price?address={WSOL_MINT}"
    req = urllib.request.Request(
        url,
        headers={"X-API-KEY": api_key, "x-chain": "solana"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode())
        value = data.get("data", {}).get("value")
        if value is None:
            return None
        price = float(value)
        return price if price > 0 else None
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return None


def get_sol_usd(
    *,
    default: float = DEFAULT_SOL_USD,
    fetcher: Callable[[], Optional[float]] = fetch_sol_usd_birdeye,
    cache: Any = _django_cache,
) -> float:
    """Return a cached SOL/USD spot, refreshing at most once per ``CACHE_TTL_S``.

    On cache hit: returns the cached value.  On miss: calls ``fetcher`` once; a
    positive result is cached (TTL ``CACHE_TTL_S``) and returned; any failure
    falls back to ``default`` WITHOUT caching it (so the next call retries).

    All collaborators are injected for offline testing:
        default — fallback price when no live spot is resolvable.
        fetcher — zero-arg callable returning a price or None (default: Birdeye).
        cache   — a cache object exposing ``get(key)`` and
                  ``set(key, value, timeout)`` (default: Django's cache).
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        try:
            return float(cached)
        except (TypeError, ValueError):
            pass  # corrupt cache entry — fall through to a fresh fetch

    value: Optional[float]
    try:
        value = fetcher()
    except Exception:  # noqa: BLE001 - a price miss must never break the caller
        value = None

    if value is not None and value > 0:
        cache.set(CACHE_KEY, float(value), CACHE_TTL_S)
        return float(value)

    return float(default)


def usd_from_sol(sol_amount: float, **kwargs: Any) -> float:
    """Value a SOL amount in USD using the cached spot (``get_sol_usd``).

    Convenience for the copy-trade trigger: convert a watched wallet's on-chain
    SOL buy size to USD for the ``min_trigger_buy_usd`` gate.  Extra kwargs are
    forwarded to ``get_sol_usd`` (e.g. an injected fetcher/cache in tests).
    """
    return float(sol_amount) * get_sol_usd(**kwargs)


__all__ = [
    "WSOL_MINT",
    "DEFAULT_SOL_USD",
    "CACHE_KEY",
    "CACHE_TTL_S",
    "fetch_sol_usd_birdeye",
    "get_sol_usd",
    "usd_from_sol",
]
