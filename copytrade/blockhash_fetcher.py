# ---
# module: copytrade.blockhash_fetcher
# sprint: feat/copy-live-exec-curve-ix
# story: copy-live-exec
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: json, urllib, typing
# ---
"""Minimal getLatestBlockhash fetcher for the bonding-curve live path.

CAPITAL SAFETY:
  - NO sends — pure read-only JSON-RPC call.
  - The *fetcher* parameter is injectable for offline tests: pass a callable
    that returns a dict without any network access (same discipline as
    copytrade.curve_price._default_rpc_fetcher / price_source tests).
  - Called ONLY when trading_enabled=True (the caller gates); the observe
    path never imports or invokes this module.

Endpoint: Helius STANDARD RPC (https://mainnet.helius-rpc.com), NOT the
Sender /fast or atlas endpoint.  Consistent with curve_price.HELIUS_RPC_BASE.

Ported from solanaBilly app/tasks/trading_tasks.py::_rpc_get_latest_blockhash
(~line 1151): getLatestBlockhash JSON-RPC, parse result.value.blockhash.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

#: Helius mainnet standard endpoint (same as curve_price.HELIUS_RPC_BASE).
HELIUS_RPC_BASE: str = "https://mainnet.helius-rpc.com"


def _default_blockhash_fetcher(rpc_url: str, timeout_s: float) -> dict[str, Any] | None:
    """POST getLatestBlockhash via urllib.  Returns parsed JSON dict or None on error."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getLatestBlockhash",
            "params": [{"commitment": "confirmed"}],
        }
    ).encode()
    req = urllib.request.Request(
        rpc_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError, TypeError):
        return None


def get_latest_blockhash(
    rpc_url: str,
    *,
    fetcher: Optional[Callable[[str, float], Optional[dict[str, Any]]]] = None,
    timeout_s: float = 5.0,
) -> str:
    """Fetch the latest confirmed blockhash from the Helius standard RPC.

    Returns the base58 blockhash string from the ``result.value.blockhash``
    field of the getLatestBlockhash JSON-RPC response.

    The *fetcher* parameter accepts a callable ``(rpc_url, timeout_s) -> dict|None``
    for offline unit tests that must not touch the network.

    Args:
        rpc_url:    Full Helius standard RPC URL (includes API key query param).
                    Example: "https://mainnet.helius-rpc.com/?api-key=<KEY>"
        fetcher:    Injectable fetcher for offline tests.  None -> live urllib.
        timeout_s:  HTTP request timeout in seconds (default 5.0).

    Returns:
        Base58 blockhash string.

    Raises:
        RuntimeError: If the RPC call fails, returns an error, or the blockhash
                      field is absent/empty.
    """
    _fetch = fetcher if fetcher is not None else _default_blockhash_fetcher
    try:
        data = _fetch(rpc_url, timeout_s)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"getLatestBlockhash network error: {exc}") from exc

    if data is None:
        raise RuntimeError("getLatestBlockhash: network error (fetcher returned None)")

    if "error" in data:
        raise RuntimeError(f"getLatestBlockhash RPC error: {data['error']}")

    try:
        blockhash = data["result"]["value"]["blockhash"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError(
            f"getLatestBlockhash: unexpected response shape: {data!r}"
        ) from exc

    if not blockhash or not isinstance(blockhash, str):
        raise RuntimeError(f"getLatestBlockhash: empty or invalid blockhash: {blockhash!r}")

    return blockhash


__all__ = [
    "HELIUS_RPC_BASE",
    "get_latest_blockhash",
]
