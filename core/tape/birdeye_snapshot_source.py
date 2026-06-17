# ---
# module: core.tape.birdeye_snapshot_source
# sprint: sprint-8
# story: US-37 AC-37.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.snapshot_source, urllib.request, json, datetime
# ---
"""BirdeyeSnapshotSource — concrete SnapshotDataSource backed by Birdeye REST APIs.

This class is the live adapter for on-demand score-time snapshots.  It lives
ONLY in the adapter layer (core/tape/) and is injected into SnapshotFetcher at
startup — it is NEVER imported by any core module (snapshot_fetcher.py,
snapshot_source.py, etc.).

The US-2 static-analysis guard (test_snapshot_fetcher_ac241.py) scans
core/snapshot_fetcher.py for concrete-source imports — this file contains none.
The AC-2.2 guard (test_clock.py) scans core/ for datetime.now()/time.time()
calls — this file contains neither; as_of is received from the injected Clock
via the SnapshotFetcher, never derived here.

Three Birdeye endpoints are fanned out in a single logical read:
  /defi/token_security   — mint/freeze authority, LP-burned flag
  /defi/v3/token/holder  — holder distribution (top-N addresses + total)
  /defi/token_overview   — liquidity, TVL, market-depth proxy

All three are Professional-plan endpoints, within the project's generous
Birdeye allowance.  The #380 future-window clamp (min(as_of, now)) and the
Redis token-bucket limiter are both retained in SnapshotFetcher (the caller) —
BirdeyeSnapshotSource receives an already-clamped as_of and is rate-gated
before this call is made.
"""
import json
import logging
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.snapshot_source import SnapshotDataSource

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BIRDEYE_BASE_URL: str = "https://public-api.birdeye.so"
BIRDEYE_CHAIN: str = "solana"
_REQUEST_TIMEOUT: int = 15
_DEFAULT_HOLDER_LIMIT: int = 20


# ---------------------------------------------------------------------------
# Concrete SnapshotDataSource — Birdeye REST snapshot
# ---------------------------------------------------------------------------


class BirdeyeSnapshotSource(SnapshotDataSource):
    """Concrete SnapshotDataSource that fetches a score-time snapshot via Birdeye REST.

    Fans out three REST calls (security, holders, overview) and assembles them
    into the canonical seven-field snapshot dict expected by SnapshotSchema.

    Lives ONLY in the adapter layer (core/tape/).  Inject via
    SnapshotFetcher(source=BirdeyeSnapshotSource(api_key=...), clock=...) at
    listener startup; never import this class from core pipeline modules.

    Args:
        api_key:       Birdeye API key (X-API-KEY header).
        holder_limit:  Number of top holders to fetch (default 20).
    """

    def __init__(self, api_key: str, holder_limit: int = _DEFAULT_HOLDER_LIMIT) -> None:
        self._api_key = api_key
        self._holder_limit = holder_limit

    # ------------------------------------------------------------------
    # SnapshotDataSource interface
    # ------------------------------------------------------------------

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        """Fetch the score-time snapshot for *mint* as of *as_of*.

        as_of is passed through from SnapshotFetcher after the #380 future-
        window clamp (min(as_of, clock.now())) has already been applied.
        This method does NOT call datetime.now() — time is owned by the caller.

        Returns a dict with keys matching SnapshotSchema.from_raw():
            holder_distribution, mint_authority, freeze_authority,
            lp_burned, liquidity, tvl, depth
        """
        security = self._fetch_token_security(mint)
        holders = self._fetch_token_holders(mint)
        overview = self._fetch_token_overview(mint)

        sec = security.get("data") or {}
        hld = holders.get("data") or {}
        ov = overview.get("data") or {}

        return {
            "holder_distribution": {
                "items": hld.get("items", []),
                "total": hld.get("total", 0),
            },
            "mint_authority": sec.get("mintAuthority"),
            "freeze_authority": sec.get("freezeAuthority"),
            "lp_burned": bool(sec.get("lpBurned", False)),
            "liquidity": float(ov.get("liquidity") or 0.0),
            "tvl": float(ov.get("realLiquidity") or ov.get("liquidity") or 0.0),
            "depth": {
                "buy": float(ov.get("buy24h") or ov.get("buy") or 0.0),
                "sell": float(ov.get("sell24h") or ov.get("sell") or 0.0),
            },
        }

    # ------------------------------------------------------------------
    # Internal REST helpers
    # ------------------------------------------------------------------

    def _fetch_token_security(self, mint: str) -> dict:
        """GET /defi/token_security — mint/freeze authority + LP-burned flag."""
        return self._call("/defi/token_security", {"address": mint})

    def _fetch_token_holders(self, mint: str) -> dict:
        """GET /defi/v3/token/holder — top-N holder distribution."""
        return self._call(
            "/defi/v3/token/holder",
            {"address": mint, "limit": self._holder_limit},
        )

    def _fetch_token_overview(self, mint: str) -> dict:
        """GET /defi/token_overview — liquidity, TVL, and market-depth proxy."""
        return self._call("/defi/token_overview", {"address": mint})

    def _call(self, path: str, params: dict) -> dict:
        """HTTP GET to Birdeye REST, returning the parsed JSON response dict.

        Adds X-API-KEY and x-chain headers required by the Birdeye Professional
        API.  Raises RuntimeError on HTTP or network error so the caller can
        decide whether to retry or surface the failure.

        This method is the single mockable seam for offline tests.
        """
        url = f"{BIRDEYE_BASE_URL}{path}?{urlencode(params)}"
        req = Request(url)
        req.add_header("X-API-KEY", self._api_key)
        req.add_header("x-chain", BIRDEYE_CHAIN)
        req.add_header("Accept", "application/json")
        try:
            with urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            logger.error("Birdeye REST %s HTTP %s: %s", path, exc.code, exc.reason)
            raise RuntimeError(
                f"Birdeye REST {path} returned HTTP {exc.code}"
            ) from exc
        except URLError as exc:
            logger.error("Birdeye REST %s URLError: %s", path, exc.reason)
            raise RuntimeError(
                f"Birdeye REST {path} network error: {exc.reason}"
            ) from exc
