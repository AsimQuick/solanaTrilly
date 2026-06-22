# ---
# module: copytrade.birdeye_tape
# epic: EPIC-copy-curvestage-integration
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: urllib (stdlib), json, time, django.conf.settings
# ---
"""Birdeye seek_by_time token-tape client for the curve-stage copy engine.

Fetches a single mint's swap tape over an arbitrary ``[t_from, t_to]`` window and
returns the RAW Birdeye items.  The caller maps them via
``copytrade.entry_features.birdeye_items_to_owner_tape`` (whose 6-tuple output
feeds BOTH the gate's ``entry_features`` and the settler's ``settle_grad`` — the
first 4 tuple elements are exactly the ``(t, price, usd, side)`` the settler reads).

WHY BIRDEYE (parity, tester G2/B5): the seed classifier and the lab's honest PnL
were both built on Birdeye USD prices (basePrice*quotePrice).  Sourcing the live
tape from the SAME endpoint keeps the gate features AND the reconstructed PnL on
the lab's basis — NOT the recorder's Helius vsol/vtok SOL-ratio price.

ISOLATION (§5): self-contained — urllib + Django settings only, no core.* / no
firehose imports.  BLOCKING: the caller MUST run this off the asyncio event loop
(executor + a concurrency cap), per the #377 loop-starvation lesson.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

_SEEK_BY_TIME_URL = "https://public-api.birdeye.so/defi/txs/token/seek_by_time"
_MAX_ITERS = 40                 # credit-safety pagination cap (matches the lab probe)
_OFFSET_RESET_THRESHOLD = 9800  # Birdeye offset cap -> reset cursor to last bt
_SERVER_ERR_SLEEP_FACTOR = 1.5


def _get_api_key() -> str:
    from django.conf import settings  # noqa: PLC0415

    return str(getattr(settings, "BIRDEYE_API_KEY", "") or "")


def _be_get(params: dict[str, Any], api_key: str, *, tries: int = 5) -> dict[str, Any]:
    """GET seek_by_time with retry/backoff (urllib; requests is not in the image).

    Returns the parsed JSON, or ``{"_err": ...}`` on a terminal error (callers
    treat that as an empty page and stop)."""
    headers = {"X-API-KEY": api_key, "x-chain": "solana"}
    url = f"{_SEEK_BY_TIME_URL}?{urllib.parse.urlencode(params)}"
    for i in range(tries):
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
            try:
                return json.loads(raw)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[copytrade.birdeye_tape] JSON decode error: %s", exc)
                return {"_err": "json_error"}
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status == 429:
                ra = float((exc.headers.get("Retry-After", 2) if exc.headers else 2) or 2) + 1.0
                time.sleep(ra)
                continue
            if status >= 500:
                time.sleep(_SERVER_ERR_SLEEP_FACTOR * (i + 1))
                continue
            return {"_err": status}
        except (urllib.error.URLError, OSError) as exc:
            logger.debug("[copytrade.birdeye_tape] request error (attempt %d): %s", i + 1, exc)
            time.sleep(_SERVER_ERR_SLEEP_FACTOR * (i + 1))
            continue
    return {"_err": "exhausted"}


def fetch_token_tape(
    mint: str, t_from: int, t_to: int, *, api_key: str | None = None,
) -> list[dict]:
    """Return RAW Birdeye seek_by_time swap items for *mint* in ``[t_from, t_to]``.

    Forward-paginated, ascending by blockUnixTime; items past ``t_to`` end the scan.
    Empty list on a missing key / no rows / HTTP exhaustion (never raises — the
    caller fails-closed on an empty tape, never guessing a score/PnL).
    """
    key = api_key if api_key is not None else _get_api_key()
    if not key:
        logger.warning("[copytrade.birdeye_tape] mint=%s BIRDEYE_API_KEY not set — empty tape.", mint)
        return []
    t_from = int(t_from)
    t_to = int(t_to)
    items: list[dict] = []
    offset = 0
    after = t_from
    iters = 0
    while iters < _MAX_ITERS:
        iters += 1
        body = _be_get(
            {"address": mint, "after_time": after, "offset": offset, "limit": 100, "tx_type": "swap"},
            key,
        )
        if "_err" in body:
            logger.warning(
                "[copytrade.birdeye_tape] mint=%s HTTP %s at iter=%d — returning %d items.",
                mint, body["_err"], iters, len(items),
            )
            break
        page = (body.get("data") or {}).get("items") or []
        if not page:
            break
        last_bt = t_from
        stop = False
        for it in page:
            bt = it.get("blockUnixTime")
            if bt is None:
                continue
            last_bt = int(bt)
            if last_bt > t_to:
                stop = True
                break
            items.append(it)
        if stop or len(page) < 100:
            break
        offset += len(page)
        if offset >= _OFFSET_RESET_THRESHOLD:
            after = last_bt
            offset = 0
    return items
