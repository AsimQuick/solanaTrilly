# ---
# module: core.detection.birdeye_sweep
# sprint: sprint-4
# story: US-16 AC-16.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.models, datetime, django.conf, django.utils, urllib.request, json
# ---
"""Birdeye REST graduation sweep — third-belt reconciler (AC-16.2).

Fetches recently graduated tokens from the Birdeye REST API and reconciles
any that are missing from the local tokens table.  Uses get_or_create (NOT
update_or_create) so existing rows written by the MEME consumer or the
Helius MigrateReconciler are never overwritten.

Public API:
  reconcile_graduation_events(events) — pure DB reconciler; callable from
      tests without a live API connection.
  fetch_birdeye_recent_graduations() — live Birdeye REST call; returns []
      when BIRDEYE_API_KEY is absent or the request fails (fail-safe).
"""
from datetime import datetime, timezone


def reconcile_graduation_events(events: list[dict]) -> int:
    """Idempotently reconcile a list of graduation event dicts into Token rows.

    Each dict must contain 'mint' (str).  Optional keys: 'pool_address' (str),
    'block_time' (int — Unix epoch seconds).

    Uses get_or_create so that a duplicate call for the same mint creates ZERO
    additional rows, and any row written by another reconciler is not overwritten.

    Returns the count of newly-created Token rows.
    """
    from django.utils import timezone as tz  # lazy — avoids import-time Django setup

    from core.models import Token  # lazy — avoids circular imports at load time

    created_count = 0
    for event in events:
        mint: str = event["mint"]
        pool_address: str = event.get("pool_address", "")

        block_time = event.get("block_time")
        if block_time is not None:
            graduated_at = datetime.fromtimestamp(int(block_time), tz=timezone.utc)
            graduated_block_time = int(block_time)
        else:
            graduated_at = tz.now()
            graduated_block_time = int(graduated_at.timestamp())

        _, created = Token.objects.get_or_create(
            mint=mint,
            defaults={
                "pool_address": pool_address,
                "graduated_at": graduated_at,
                "graduated_block_time": graduated_block_time,
                "dex_source": "birdeye_sweep",
                "raw_graduation": event,
            },
        )
        if created:
            created_count += 1

    return created_count


def fetch_birdeye_recent_graduations() -> list[dict]:
    """Fetch recently graduated tokens from the Birdeye REST API.

    Returns a list of graduation dicts with keys: mint, pool_address, block_time.
    Returns an empty list when BIRDEYE_API_KEY is not configured or any error
    occurs — the sweep is a best-effort reconciler, not a hard requirement.
    """
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    from django.conf import settings

    api_key: str = getattr(settings, "BIRDEYE_API_KEY", "")
    if not api_key:
        return []

    params = urllib.parse.urlencode(
        {"sort_by": "mc", "sort_type": "desc", "offset": 0, "limit": 50}
    )
    url = f"https://public-api.birdeye.so/defi/v3/token/new_listing?{params}"
    req = urllib.request.Request(
        url,
        headers={"X-API-KEY": api_key, "x-chain": "solana"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("data", {}).get("items", [])
        return [
            {
                "mint": item["address"],
                "pool_address": item.get("liquidity_address", ""),
                "block_time": item.get("liquidity_added_at"),
            }
            for item in items
            if item.get("address")
        ]
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return []
