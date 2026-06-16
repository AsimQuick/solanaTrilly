# ---
# module: core.tests.test_raw_immutable_ac272
# sprint: sprint-6
# story: US-27 AC-27.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.snapshot_fetcher, core.replay_snapshot_source, core.snapshot_schema,
#               core.models, core.encoders, pytest, datetime, json
# ---
"""AC-27.2 — Raw = Immutable Truth.

Stored snapshot == verbatim raw payload (json-safe-normalized).
SnapshotSchema.from_raw() re-derives all 7 fields from stored raw WITHOUT a
second DataSource call.

Invariants verified:
  1. Stored snap.raw equals the json-safe-normalized source payload.
  2. For a pure-JSON-safe payload, snap.raw == fixture_payload verbatim.
  3. SnapshotSchema.from_raw(snap.raw) maps all 7 fields correctly without
     triggering a second DataSource call.
  4-6. Each of the three first-class fields (holder_distribution, lp_burned,
       liquidity/tvl/depth) re-derives correctly.
  7. Calling from_raw() does not mutate the stored raw JSONB.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from core.clock import VirtualClock
from core.encoders import JsonSafeEncoder
from core.replay_snapshot_source import ReplaySnapshotSource
from core.schemas import PipelineConfigSchema
from core.score_orchestrator import ScoreTimeOrchestrator
from core.snapshot_fetcher import SnapshotFetcher
from core.snapshot_schema import SnapshotSchema

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
SCORE_AT_S = 120
MINT = "AC272ImmutableMint111111111111111111111111111"

FIXTURE_PAYLOAD: dict = {
    "holder_distribution": {"top10": 0.42, "top25": 0.61, "count": 950},
    "mint_authority": None,
    "freeze_authority": None,
    "lp_burned": True,
    "liquidity": 7200.0,
    "tvl": 7100.0,
    "depth": {"bid": 200.0, "ask": 195.0},
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> PipelineConfigSchema:
    return PipelineConfigSchema.from_model_sections(
        detection={},
        tape={"idle_kill_ttl_s": 7200},
        scoring={
            "score_at_elapsed_s": SCORE_AT_S,
            "window_s": SCORE_AT_S + 180,
            "capture_buffer_s": 4,
        },
        outcome={"window_s": 3600},
        trading={},
    )


def _store_snapshot(mint: str, payload: dict):
    """Run the full fetch_and_persist path for mint and return the source used."""
    clock = VirtualClock(T0 + timedelta(seconds=SCORE_AT_S))
    source = ReplaySnapshotSource({mint: payload})
    fetcher = SnapshotFetcher(source=source, clock=clock)
    orch = ScoreTimeOrchestrator(
        fetcher=fetcher,
        clock=clock,
        config_fn=_make_config,
    )
    orch.orchestrate(mint, T0)
    return source


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_stored_raw_equals_json_safe_normalized_source():
    """Stored snap.raw equals the json-safe-normalized source payload."""
    from core.models import Snapshot

    _store_snapshot(MINT, FIXTURE_PAYLOAD)

    normalized = json.loads(json.dumps(FIXTURE_PAYLOAD, cls=JsonSafeEncoder))
    snap = Snapshot.objects.get(mint=MINT)

    assert snap.raw == normalized, (
        "Stored raw must equal the json-safe-normalized source payload (AC-27.2)"
    )


@pytest.mark.django_db
def test_raw_is_verbatim_source_payload():
    """For a pure-JSON-safe payload, snap.raw equals fixture_payload verbatim."""
    from core.models import Snapshot

    _store_snapshot(MINT, FIXTURE_PAYLOAD)

    snap = Snapshot.objects.get(mint=MINT)
    assert snap.raw == FIXTURE_PAYLOAD, (
        "Pure-JSON-safe payload must survive the round-trip unchanged (AC-27.2)"
    )


@pytest.mark.django_db
def test_schema_re_derives_from_stored_raw_without_second_fetch():
    """SnapshotSchema.from_raw(snap.raw) maps all 7 fields without a second DataSource call."""
    from core.models import Snapshot

    source = _store_snapshot(MINT, FIXTURE_PAYLOAD)
    call_count_after_store = source.call_count

    snap = Snapshot.objects.get(mint=MINT)
    schema = SnapshotSchema.from_raw(snap.raw)

    assert source.call_count == call_count_after_store, (
        "from_raw() must not trigger a second DataSource call; "
        f"call_count went from {call_count_after_store} to {source.call_count}"
    )
    assert schema.holder_distribution == FIXTURE_PAYLOAD["holder_distribution"]
    assert schema.mint_authority == FIXTURE_PAYLOAD["mint_authority"]
    assert schema.freeze_authority == FIXTURE_PAYLOAD["freeze_authority"]
    assert schema.lp_burned == FIXTURE_PAYLOAD["lp_burned"]
    assert schema.liquidity == FIXTURE_PAYLOAD["liquidity"]
    assert schema.tvl == FIXTURE_PAYLOAD["tvl"]
    assert schema.depth == FIXTURE_PAYLOAD["depth"]


@pytest.mark.django_db
def test_schema_re_derives_holder_distribution_from_raw():
    """schema.holder_distribution == fixture_payload['holder_distribution']."""
    from core.models import Snapshot

    _store_snapshot(MINT, FIXTURE_PAYLOAD)
    snap = Snapshot.objects.get(mint=MINT)
    schema = SnapshotSchema.from_raw(snap.raw)

    assert schema.holder_distribution == FIXTURE_PAYLOAD["holder_distribution"]


@pytest.mark.django_db
def test_schema_re_derives_lp_burned_from_raw():
    """schema.lp_burned == fixture_payload['lp_burned']."""
    from core.models import Snapshot

    _store_snapshot(MINT, FIXTURE_PAYLOAD)
    snap = Snapshot.objects.get(mint=MINT)
    schema = SnapshotSchema.from_raw(snap.raw)

    assert schema.lp_burned == FIXTURE_PAYLOAD["lp_burned"]


@pytest.mark.django_db
def test_schema_re_derives_liquidity_tvl_depth_from_raw():
    """All three first-class fields (liquidity, tvl, depth) re-derive from stored raw."""
    from core.models import Snapshot

    _store_snapshot(MINT, FIXTURE_PAYLOAD)
    snap = Snapshot.objects.get(mint=MINT)
    schema = SnapshotSchema.from_raw(snap.raw)

    assert schema.liquidity == FIXTURE_PAYLOAD["liquidity"], (
        f"liquidity mismatch: {schema.liquidity!r} != {FIXTURE_PAYLOAD['liquidity']!r}"
    )
    assert schema.tvl == FIXTURE_PAYLOAD["tvl"], (
        f"tvl mismatch: {schema.tvl!r} != {FIXTURE_PAYLOAD['tvl']!r}"
    )
    assert schema.depth == FIXTURE_PAYLOAD["depth"], (
        f"depth mismatch: {schema.depth!r} != {FIXTURE_PAYLOAD['depth']!r}"
    )


@pytest.mark.django_db
def test_raw_not_mutated_by_schema_re_derivation():
    """After calling from_raw(snap.raw), snap.raw in the DB is unchanged."""
    from core.models import Snapshot

    _store_snapshot(MINT, FIXTURE_PAYLOAD)
    snap = Snapshot.objects.get(mint=MINT)
    raw_before = json.dumps(snap.raw, sort_keys=True)

    SnapshotSchema.from_raw(snap.raw)

    snap.refresh_from_db()
    raw_after = json.dumps(snap.raw, sort_keys=True)

    assert raw_before == raw_after, (
        "from_raw() must not mutate the stored raw JSONB (AC-27.2)"
    )
