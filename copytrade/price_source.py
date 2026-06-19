# ---
# module: copytrade.price_source
# sprint: cutover (copy-trade live)
# story: copytrade-runtime
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: django.conf.settings, urllib
# ---
"""Per-mint price fetcher — copytrade-owned Birdeye REST lookup (§5 isolated).

Fetches the current USD price for any mint via the Birdeye REST price endpoint.
This is a copytrade-OWNED module (not shared with the firehose) — it must NOT
import BirdeyeSwapSource, Birdeye*Source, or any firehose module (§5 isolation).

Design mirrors core/pricing/sol_usd.py:
  - urllib only (no requests library dependency)
  - Injected fetcher for offline testing (no network in tests)
  - FAIL-SAFE: returns None on any failure — callers fall back to raw event price
  - No datetime.now() / time.time() (clock discipline)

The isolation contract (enforced by the topology test) means this module is the
ONLY price-fetch path the copytrade engine uses; it does NOT go through
core.tape.birdeye_swap_source.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from django.conf import settings

#: Birdeye REST price endpoint — same base as core/pricing/sol_usd.py.
BIRDEYE_PRICE_URL: str = "https://public-api.birdeye.so/defi/price"


def _default_fetcher(
    url: str,
    api_key: str,
    timeout_s: float,
) -> dict[str, Any] | None:
    """Perform the HTTP GET and return parsed JSON, or None on any error.

    Separated from fetch_mint_price_usd so tests can inject a fake without
    needing to mock urllib at the module level.
    """
    req = urllib.request.Request(
        url,
        headers={"X-API-KEY": api_key, "x-chain": "solana"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


def fetch_mint_price_usd(
    mint: str,
    *,
    timeout_s: float = 8.0,
    fetcher: Optional[Callable[[str, str, float], Optional[dict[str, Any]]]] = None,
) -> Optional[float]:
    """Return the current USD price for *mint* from Birdeye REST, or None.

    Calls ``GET /defi/price?address=<mint>`` with the project's BIRDEYE_API_KEY
    and parses ``data.data.value``.

    Returns
    -------
    float
        The current USD price when the request succeeds and the value is positive.
    None
        On ANY failure: missing API key, network error, malformed payload,
        zero/negative price, missing data field.  The caller MUST handle None
        (typically by falling back to the raw event's reported price).

    Parameters
    ----------
    mint:
        The Solana token mint address to price.
    timeout_s:
        HTTP request timeout in seconds (default 8.0).
    fetcher:
        Optional injected callable ``(url, api_key, timeout_s) -> dict | None``
        for offline testing.  Defaults to the real urllib GET.
    """
    api_key: str = getattr(settings, "BIRDEYE_API_KEY", "")
    if not api_key:
        return None
    if not mint or not mint.strip():
        return None

    url = f"{BIRDEYE_PRICE_URL}?address={mint}"
    _fetch = fetcher if fetcher is not None else _default_fetcher

    try:
        payload = _fetch(url, api_key, timeout_s)
    except Exception:  # noqa: BLE001 — price miss must never break the caller
        return None

    if payload is None:
        return None

    try:
        value = payload.get("data", {}).get("value")
        if value is None:
            return None
        price = float(value)
        return price if price > 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


__all__ = ["BIRDEYE_PRICE_URL", "fetch_mint_price_usd"]
