# ---
# module: core.tests.test_sol_usd_spot
# sprint: cutover (copy-trade live)
# story: shared-sol-usd-spot
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: pytest
# ---
"""Offline tests for the shared SOL/USD cached-spot helper (zero network)."""
import pytest

from core.pricing.sol_usd import (
    CACHE_KEY,
    DEFAULT_SOL_USD,
    get_sol_usd,
    usd_from_sol,
)


class FakeCache:
    """Minimal cache stub exposing get/set with the Django cache signature."""

    def __init__(self) -> None:
        self.store: dict = {}
        self.set_calls = 0

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value, timeout=None):
        self.set_calls += 1
        self.store[key] = value


def test_fetches_once_then_serves_from_cache():
    cache = FakeCache()
    calls = []

    def fetch():
        calls.append(1)
        return 152.5

    assert get_sol_usd(fetcher=fetch, cache=cache) == 152.5
    # Second call must hit the cache, NOT the fetcher.
    assert get_sol_usd(fetcher=fetch, cache=cache) == 152.5
    assert len(calls) == 1
    assert cache.store[CACHE_KEY] == 152.5


def test_falls_back_to_default_and_does_not_cache_on_none():
    cache = FakeCache()
    assert get_sol_usd(fetcher=lambda: None, cache=cache, default=140.0) == 140.0
    # The fallback is NOT cached — the next call retries the live fetch.
    assert cache.get(CACHE_KEY) is None
    assert cache.set_calls == 0


def test_falls_back_to_default_on_fetcher_exception():
    cache = FakeCache()

    def boom():
        raise RuntimeError("network down")

    assert get_sol_usd(fetcher=boom, cache=cache, default=137.0) == 137.0
    assert cache.get(CACHE_KEY) is None


def test_rejects_nonpositive_prices():
    cache = FakeCache()
    assert get_sol_usd(fetcher=lambda: 0.0, cache=cache, default=140.0) == 140.0
    assert get_sol_usd(fetcher=lambda: -5.0, cache=cache, default=140.0) == 140.0
    assert cache.get(CACHE_KEY) is None


def test_default_constant_matches_legacy_hardcode():
    # The fallback must equal the value formerly inlined in run_firehose.
    assert DEFAULT_SOL_USD == 140.0


def test_usd_from_sol_uses_spot():
    cache = FakeCache()
    # 1.5 SOL * 200 USD/SOL = 300 USD
    assert usd_from_sol(1.5, fetcher=lambda: 200.0, cache=cache) == pytest.approx(300.0)
