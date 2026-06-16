# ---
# module: core.tests.test_snapshot_persist_ac242
# sprint: sprint-6
# story: US-24 AC-24.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_fetcher, core.snapshot_source, core.clock,
#               core.models, core.snapshot_schema, pytest, datetime, decimal
# ---
"""AC-24.2 — SnapshotFetcher persists raw payload to the 'snapshots' row.

Verifies that fetch_and_persist() stores the score-time fields the tape
cannot give (holder distribution, mint/freeze authority, lp_burned flag,
liquidity/TVL/depth — §6.3, §1.1) verbatim as immutable JSONB (§6.4.1)
via the §8 'snapshots' row, using JsonSafeEncoder (H3/US-5 guard).

Tests:
  1. fetch_and_persist() returns the raw payload.
  2. Exactly one 'snapshots' row is persisted.
  3. The persisted raw payload is byte-intact (matches the source payload).
  4. taken_at equals the injected clock's now().
  5. elapsed_s matches the value passed to fetch_and_persist().
  6. SnapshotSchema.from_raw() re-derives all seven fields from the stored raw
     (the typed schema AC-23.3 roundtrips through the DB row).
  7. A second call for the same mint returns None and still yields exactly one row
     (at-most-one, §6.3 — fetcher + DB unique constraint both enforced).
  8. JsonSafeEncoder guard: a payload containing NaN/Inf/Decimal persists
     without error and reads back json-safe from the DB.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from core.clock import VirtualClock
from core.models import Snapshot
from core.snapshot_fetcher import SnapshotFetcher
from core.snapshot_schema import SnapshotSchema
from core.snapshot_source import SnapshotDataSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
TEST_MINT = "AC242TestMint1111111111111111111111111111111"
ELAPSED_S = 60

SAMPLE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.45, "top20": 0.62, "count": 1234},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 5000.0,
    "tvl": 4800.0,
    "depth": {"bid": 120.0, "ask": 115.0},
}

# ---------------------------------------------------------------------------
# In-memory DataSource test double (schema-faithful, no network calls)
# ---------------------------------------------------------------------------


class InMemorySnapshotSource(SnapshotDataSource):
    """Replay-style test double — returns pre-loaded snapshot dicts."""

    def __init__(self, payloads: dict) -> None:
        self._payloads = payloads
        self.call_count: int = 0

    def get_snapshot(self, mint: str, as_of: datetime) -> dict:
        self.call_count += 1
        return self._payloads[mint]


# ---------------------------------------------------------------------------
# Helper: create a fresh fetcher + source for each test
# ---------------------------------------------------------------------------


def _make_fetcher(payloads: dict | None = None) -> tuple[SnapshotFetcher, InMemorySnapshotSource]:
    if payloads is None:
        payloads = {TEST_MINT: SAMPLE_PAYLOAD}
    source = InMemorySnapshotSource(payloads)
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))
    return fetcher, source


# ---------------------------------------------------------------------------
# 1. fetch_and_persist() return value
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_and_persist_returns_raw_payload():
    """fetch_and_persist() returns the raw dict from the DataSource."""
    fetcher, _ = _make_fetcher()
    result = fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    assert result == SAMPLE_PAYLOAD


# ---------------------------------------------------------------------------
# 2. Exactly one row persisted
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_and_persist_creates_exactly_one_row():
    """fetch_and_persist() creates exactly one 'snapshots' row for the mint."""
    fetcher, _ = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    assert Snapshot.objects.filter(mint=TEST_MINT).count() == 1


# ---------------------------------------------------------------------------
# 3. Raw payload is intact in the DB
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_and_persist_raw_payload_is_intact():
    """The persisted raw JSONB equals the source payload (§6.4.1 verbatim)."""
    fetcher, _ = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    stored = Snapshot.objects.get(mint=TEST_MINT).raw
    assert stored == SAMPLE_PAYLOAD


# ---------------------------------------------------------------------------
# 4. taken_at equals clock.now()
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_and_persist_taken_at_equals_clock_now():
    """taken_at in the persisted row equals the injected clock's now() (Principle #7)."""
    fetcher, _ = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    stored = Snapshot.objects.get(mint=TEST_MINT)
    assert stored.taken_at == T0


# ---------------------------------------------------------------------------
# 5. elapsed_s matches the caller-supplied value
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_and_persist_elapsed_s_matches_argument():
    """elapsed_s in the persisted row equals the value passed to fetch_and_persist()."""
    fetcher, _ = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    stored = Snapshot.objects.get(mint=TEST_MINT)
    assert stored.elapsed_s == ELAPSED_S


# ---------------------------------------------------------------------------
# 6. SnapshotSchema re-derives from stored raw (AC-23.3 roundtrip)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_snapshot_schema_rederives_from_stored_raw():
    """SnapshotSchema.from_raw() re-derives all seven fields from the persisted row
    without a second fetch — raw is the immutable source of truth (§6.4.1).
    """
    fetcher, source = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)

    stored_raw = Snapshot.objects.get(mint=TEST_MINT).raw
    call_count_before = source.call_count  # should be 1 (from the fetch)

    schema = SnapshotSchema.from_raw(stored_raw)

    # No second fetch — re-derivation reads stored raw only
    assert source.call_count == call_count_before, (
        "SnapshotSchema.from_raw() must not trigger a second DataSource call"
    )

    assert schema.holder_distribution == SAMPLE_PAYLOAD["holder_distribution"]
    assert schema.mint_authority is None
    assert schema.freeze_authority is None
    assert schema.lp_burned is True
    assert schema.liquidity == SAMPLE_PAYLOAD["liquidity"]
    assert schema.tvl == SAMPLE_PAYLOAD["tvl"]
    assert schema.depth == SAMPLE_PAYLOAD["depth"]


# ---------------------------------------------------------------------------
# 7. At-most-one: second call returns None, row count stays 1
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_second_fetch_and_persist_returns_none():
    """A second fetch_and_persist() for the same mint returns None (at-most-one §6.3)."""
    fetcher, _ = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    second = fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    assert second is None


@pytest.mark.django_db
def test_second_fetch_and_persist_does_not_add_row():
    """A second fetch_and_persist() for the same mint leaves exactly one row."""
    fetcher, _ = _make_fetcher()
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    fetcher.fetch_and_persist(TEST_MINT, elapsed_s=ELAPSED_S)
    assert Snapshot.objects.filter(mint=TEST_MINT).count() == 1


# ---------------------------------------------------------------------------
# 8. JsonSafeEncoder guard: NaN/Inf/Decimal persists json-safe
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_fetch_and_persist_nan_inf_decimal_are_json_safe():
    """A payload with NaN, Inf, and Decimal persists without error and reads back
    json-safe (NaN/Inf → null, Decimal → float) — the H3/US-5 guard stays green.
    """
    unsafe_mint = "AC242UnsafeMint1111111111111111111111111111"
    unsafe_payload = {
        "holder_distribution": {"top10": float("nan"), "count": 500},
        "mint_authority": None,
        "freeze_authority": None,
        "lp_burned": False,
        "liquidity": float("inf"),
        "tvl": Decimal("4999.99"),
        "depth": {"bid": float("-inf"), "ask": 50.0},
    }
    source = InMemorySnapshotSource({unsafe_mint: unsafe_payload})
    fetcher = SnapshotFetcher(source=source, clock=VirtualClock(T0))

    # Must not raise despite non-finite / Decimal values
    raw = fetcher.fetch_and_persist(unsafe_mint, elapsed_s=30)
    assert raw is not None

    stored = Snapshot.objects.get(mint=unsafe_mint).raw

    # NaN → null
    assert stored["holder_distribution"]["top10"] is None, "NaN must be stored as null"
    # +Inf → null
    assert stored["liquidity"] is None, "+Inf liquidity must be stored as null"
    # Decimal → float
    assert isinstance(stored["tvl"], float), "Decimal tvl must be stored as float"
    # -Inf → null
    assert stored["depth"]["bid"] is None, "-Inf depth.bid must be stored as null"
