# ---
# module: trading.tests.test_replay_api_ac731
# sprint: sprint-14
# story: US-73 AC-73.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, pytest-django, djangorestframework, trading.models, core.models,
#               trading.replay_api
# ---
"""AC-73.1 — Replay overlay DRF API tests over banked replay-sandbox + tape fixtures.

Verifies GET /api/trading/replay/overlay/?run_id=<id> over deterministic in-memory
fixtures (zero firehose — all data injected at test time, no live calls).

KEY ASSERTION (AC-73.1 isolation invariant):
  Positions rendered for a run_id come EXCLUSIVELY from trading_replay_positions
  (ReplayPosition model) — never from trading_positions (live Position model).
  A Position row with the same mint in the live table must NOT appear in the
  response for any replay run_id.

Banked replay-sandbox fixture (trading_replay_positions):
  RUN-001:
    RP1  mint=MINT_A  enterable=True   trigger=STOP_LOSS    pnl=-17.0%
    RP2  mint=MINT_B  enterable=True   trigger=TAKE_PROFIT  pnl=+86.4%
    RP3  mint=MINT_C  enterable=False  unentered_reason='dead'
  RUN-002:
    RP4  mint=MINT_A  enterable=True   trigger=RUG_PULL     pnl=-27.7%

Banked live-table fixture (trading_positions) — must NOT appear in overlay:
  P1  mint=MINT_A  source=model  status=CLOSED

Banked tape fixture (swaps table):
  SWAP-A1  mint=MINT_A  block_time=T0+30  price=4.5e-8  vol_sol=0.01  buy
  SWAP-A2  mint=MINT_A  block_time=T0+45  price=4.0e-8  vol_sol=0.005 sell
  SWAP-B1  mint=MINT_B  block_time=T0+30  price=5.0e-8  vol_sol=0.02  buy
  (MINT_C has no swap rows → empty candle list)

Test sections
-------------
  §1  Endpoint basics
        test_endpoint_returns_200
        test_missing_run_id_returns_400
        test_blank_run_id_returns_400

  §2  Sandbox isolation — the KEY AC-73.1 invariant
        test_sandbox_table_name_is_replay_positions
        test_live_table_different_from_sandbox_table
        test_live_position_not_in_overlay_response
        test_live_position_mint_excluded_from_response_positions

  §3  run_id filtering
        test_run_id_filter_returns_correct_count
        test_run_id_filter_returns_correct_mints
        test_different_run_id_not_in_run001_response
        test_unknown_run_id_returns_empty_positions

  §4  Response structure
        test_response_has_required_top_level_keys
        test_run_id_in_response_matches_query_param
        test_total_positions_matches_positions_list_length

  §5  Position fields
        test_position_has_all_required_fields
        test_enterable_position_has_entry_exit_markers
        test_unentered_position_included_in_response
        test_unentered_position_has_null_entry_exit

  §6  Candle basis from recorded tape
        test_candles_by_mint_keys_cover_all_position_mints
        test_candles_present_when_swap_rows_exist
        test_candles_empty_when_no_swap_rows_for_mint
        test_candle_entries_have_required_fields

  §7  Empty state
        test_empty_run_id_no_candles
        test_empty_run_id_zero_total_positions
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from rest_framework.test import APIClient

from core.models import Swap
from trading.models import Position, ReplayPosition

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

URL = "/api/trading/replay/overlay/"
RUN_001 = "replay-run-001-ac731"
RUN_002 = "replay-run-002-ac731"
RUN_UNKNOWN = "replay-run-unknown-000"

# --- Mint addresses (deterministic, zero firehose) ---
MINT_A = "MintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MINT_B = "MintBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
MINT_C = "MintCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"

# --- Timestamps ---
T0 = 1_781_289_900  # Unix epoch base (block_time anchor)
ENTRY_TS_A = datetime(2026, 6, 12, 10, 0, 30, tzinfo=timezone.utc)
EXIT_TS_A = datetime(2026, 6, 12, 10, 2, 41, tzinfo=timezone.utc)
ENTRY_TS_B = datetime(2026, 6, 12, 10, 0, 30, tzinfo=timezone.utc)
EXIT_TS_B = datetime(2026, 6, 12, 10, 1, 0, tzinfo=timezone.utc)

# Required position fields per AC-73.1
_REQUIRED_POSITION_FIELDS = {
    "id",
    "mint",
    "score",
    "enterable",
    "unentered_reason",
    "entry_ts",
    "entry_price",
    "exit_ts",
    "exit_price",
    "exit_trigger",
    "realized_pnl_pct",
    "peak_pct",
    "held_s",
    "flow_usd",
    "size_sol",
}

# Required candle fields from build_candles()
_REQUIRED_CANDLE_FIELDS = {"t", "open", "high", "low", "close", "vol", "interval_s"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def banked_sandbox(db):
    """Inject deterministic ReplayPosition rows (trading_replay_positions).

    RUN-001: 2 enterable + 1 unentered (MINT_C, enterable=False)
    RUN-002: 1 enterable with the same MINT_A (different run — isolation check)
    Zero firehose — all data hard-coded.
    """
    ReplayPosition.objects.bulk_create(
        [
            # RUN-001 — MINT_A — enterable, STOP_LOSS
            # NOTE: ReplayPosition stores held_s (duration), NOT exit_ts.
            # The API computes exit_ts = entry_ts + timedelta(seconds=held_s).
            ReplayPosition(
                replay_run_id=RUN_001,
                mint=MINT_A,
                score=1.0,
                enterable=True,
                unentered_reason=None,
                entry_ts=ENTRY_TS_A,
                entry_price=4.5e-8,
                exit_price=3.6e-8,
                exit_trigger="STOP_LOSS",
                realized_pnl_pct=-17.08,
                peak_pct=86.4,
                held_s=131.0,
                flow_usd=32987.55,
                size_sol=0.1,
            ),
            # RUN-001 — MINT_B — enterable, TAKE_PROFIT
            ReplayPosition(
                replay_run_id=RUN_001,
                mint=MINT_B,
                score=1.0,
                enterable=True,
                unentered_reason=None,
                entry_ts=ENTRY_TS_B,
                entry_price=5.0e-8,
                exit_price=6.0e-8,
                exit_trigger="TAKE_PROFIT",
                realized_pnl_pct=20.0,
                peak_pct=20.0,
                held_s=30.0,
                flow_usd=10000.0,
                size_sol=0.1,
            ),
            # RUN-001 — MINT_C — NOT enterable (dead tape)
            ReplayPosition(
                replay_run_id=RUN_001,
                mint=MINT_C,
                score=1.0,
                enterable=False,
                unentered_reason="dead",
                entry_ts=None,
                entry_price=None,
                exit_price=None,
                exit_trigger=None,
                realized_pnl_pct=None,
                peak_pct=None,
                held_s=None,
                flow_usd=None,
                size_sol=0.1,
            ),
            # RUN-002 — MINT_A — different run, same mint (isolation check)
            ReplayPosition(
                replay_run_id=RUN_002,
                mint=MINT_A,
                score=1.0,
                enterable=True,
                unentered_reason=None,
                entry_ts=ENTRY_TS_A,
                entry_price=4.5e-8,
                exit_price=3.2e-8,
                exit_trigger="RUG_PULL",
                realized_pnl_pct=-27.73,
                peak_pct=67.1,
                held_s=124.0,
                flow_usd=54330.0,
                size_sol=0.1,
            ),
        ]
    )


@pytest.fixture
def banked_live_position(db):
    """Inject a live trading_positions row for MINT_A.

    Must NOT appear in any overlay response — the live table is completely
    separate from the replay sandbox (§11.3 isolation invariant).
    """
    Position.objects.create(
        mint=MINT_A,
        source=Position.SOURCE_MODEL,
        mode=Position.MODE_OBSERVE,
        status=Position.STATUS_CLOSED,
        score=0.95,
        entry_ts=ENTRY_TS_A,
        entry_price=4.5e-8,
        size_sol=0.1,
        exit_ts=EXIT_TS_A,
        exit_price=3.6e-8,
        exit_trigger="STOP_LOSS",
        realized_pnl_sol=-0.001,
        realized_pnl_pct=-17.08,
        peak_price=4.5e-8,
        closed_at=EXIT_TS_A,
    )


@pytest.fixture
def banked_swaps(db):
    """Inject deterministic Swap rows (the existing recorded tape).

    MINT_A: 2 swaps (enough for one 15s candle)
    MINT_B: 1 swap
    MINT_C: NO swaps (→ empty candle list)
    Zero firehose — all data hard-coded.
    """
    Swap.objects.bulk_create(
        [
            # MINT_A swap 1 — buy at T0+30
            Swap(
                mint=MINT_A,
                block_time=T0 + 30,
                slot=100,
                signature="sig_a1",
                side=Swap.SIDE_BUY,
                price=4.5e-8,
                vol_sol=0.01,
                vol_usd=0.01 * 140.0,
                sol_usd=140.0,
                rel=30.0,
            ),
            # MINT_A swap 2 — sell at T0+40 (same 15s candle window)
            Swap(
                mint=MINT_A,
                block_time=T0 + 40,
                slot=110,
                signature="sig_a2",
                side=Swap.SIDE_SELL,
                price=4.0e-8,
                vol_sol=0.005,
                vol_usd=0.005 * 140.0,
                sol_usd=140.0,
                rel=40.0,
            ),
            # MINT_B swap 1 — buy at T0+30
            Swap(
                mint=MINT_B,
                block_time=T0 + 30,
                slot=100,
                signature="sig_b1",
                side=Swap.SIDE_BUY,
                price=5.0e-8,
                vol_sol=0.02,
                vol_usd=0.02 * 140.0,
                sol_usd=140.0,
                rel=30.0,
            ),
        ]
    )


# ---------------------------------------------------------------------------
# §1 Endpoint basics
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_endpoint_returns_200(banked_sandbox):
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    assert resp.status_code == 200


@pytest.mark.django_db
def test_missing_run_id_returns_400():
    client = APIClient()
    resp = client.get(URL)  # no run_id param
    assert resp.status_code == 400


@pytest.mark.django_db
def test_blank_run_id_returns_400():
    client = APIClient()
    resp = client.get(URL, {"run_id": "   "})  # whitespace only
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# §2 Sandbox isolation — KEY AC-73.1 invariant
# ---------------------------------------------------------------------------


def test_sandbox_table_name_is_replay_positions():
    """ReplayPosition.Meta.db_table must be 'trading_replay_positions' (§11.3)."""
    assert ReplayPosition._meta.db_table == "trading_replay_positions"


def test_live_table_different_from_sandbox_table():
    """The live Position table must be different from the sandbox table (§11.3)."""
    assert Position._meta.db_table != ReplayPosition._meta.db_table
    assert Position._meta.db_table == "trading_positions"


@pytest.mark.django_db
def test_live_position_not_in_overlay_response(banked_sandbox, banked_live_position):
    """A live Position row for MINT_A must never appear in the overlay response.

    This is the primary AC-73.1 isolation assertion: the API reads ONLY from
    trading_replay_positions, not trading_positions.
    """
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    assert resp.status_code == 200
    data = resp.json()
    # Verify the response is populated from the sandbox
    assert data["total_positions"] == 3  # 3 sandbox rows for RUN_001
    # Live position row has score=0.95; sandbox rows all have score=1.0
    scores = [p["score"] for p in data["positions"]]
    assert 0.95 not in scores, "Live Position row score leaked into overlay response"


@pytest.mark.django_db
def test_live_position_mint_excluded_from_response_positions(
    banked_sandbox, banked_live_position
):
    """Even though MINT_A is in both tables, the response Position rows must come
    exclusively from the sandbox (ReplayPosition), not from Position.

    Key check: source field exists only on Position — sandbox rows have no 'source'.
    """
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    for pos in data["positions"]:
        # 'source' is a field on Position (live table) — not on ReplayPosition (sandbox)
        assert "source" not in pos, (
            "Field 'source' found in response — likely reading from live table"
        )


# ---------------------------------------------------------------------------
# §3 run_id filtering
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_run_id_filter_returns_correct_count(banked_sandbox):
    """RUN-001 has 3 sandbox rows; the API must return exactly 3."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    assert data["total_positions"] == 3
    assert len(data["positions"]) == 3


@pytest.mark.django_db
def test_run_id_filter_returns_correct_mints(banked_sandbox):
    """RUN-001 must include MINT_A, MINT_B, MINT_C — and only those."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    mints_in_response = {p["mint"] for p in data["positions"]}
    assert mints_in_response == {MINT_A, MINT_B, MINT_C}


@pytest.mark.django_db
def test_different_run_id_not_in_run001_response(banked_sandbox):
    """RUN-002's MINT_A row (trigger=RUG_PULL) must not appear in RUN-001 response."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    # RUN-002 row has exit_trigger=RUG_PULL; RUN-001 MINT_A has STOP_LOSS
    triggers = [p["exit_trigger"] for p in data["positions"] if p["mint"] == MINT_A]
    assert "RUG_PULL" not in triggers, "RUN-002 row leaked into RUN-001 response"
    assert "STOP_LOSS" in triggers


@pytest.mark.django_db
def test_unknown_run_id_returns_empty_positions(db):
    """An unknown run_id returns 200 with an empty positions list."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_UNKNOWN})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_positions"] == 0
    assert data["positions"] == []


# ---------------------------------------------------------------------------
# §4 Response structure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_response_has_required_top_level_keys(banked_sandbox):
    """Response must have exactly: run_id, total_positions, positions, candles_by_mint."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    required = {"run_id", "total_positions", "positions", "candles_by_mint"}
    assert required <= set(data.keys())


@pytest.mark.django_db
def test_run_id_in_response_matches_query_param(banked_sandbox):
    """The run_id field in the response must equal the query parameter."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    assert data["run_id"] == RUN_001


@pytest.mark.django_db
def test_total_positions_matches_positions_list_length(banked_sandbox):
    """total_positions must always equal len(positions)."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    assert data["total_positions"] == len(data["positions"])


# ---------------------------------------------------------------------------
# §5 Position fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_position_has_all_required_fields(banked_sandbox):
    """Every position dict must contain all AC-73.1 required fields."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    for pos in data["positions"]:
        missing = _REQUIRED_POSITION_FIELDS - set(pos.keys())
        assert not missing, f"Position missing fields: {missing}"


@pytest.mark.django_db
def test_enterable_position_has_entry_exit_markers(banked_sandbox):
    """Enterable positions (MINT_A, MINT_B) must have non-null entry/exit markers.

    exit_ts is COMPUTED by the API as entry_ts + timedelta(seconds=held_s)
    since ReplayPosition stores held_s (duration) rather than an explicit exit_ts.
    """
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    enterable = [p for p in data["positions"] if p["enterable"]]
    assert len(enterable) == 2
    for pos in enterable:
        assert pos["entry_ts"] is not None, "entry_ts must not be null for enterable"
        assert pos["entry_price"] is not None, "entry_price must not be null"
        # exit_ts is computed from entry_ts + held_s by the API view
        assert pos["exit_ts"] is not None, "exit_ts (computed) must not be null for enterable"
        assert pos["exit_price"] is not None, "exit_price must not be null"
        assert pos["exit_trigger"] is not None, "exit_trigger must not be null"


@pytest.mark.django_db
def test_unentered_position_included_in_response(banked_sandbox):
    """Un-enterable positions (enterable=False) must be included in the response.

    The harness writes all qualifying rows to the sandbox, including ones where
    the tape was dead — the API must expose them for full audit visibility.
    """
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    unentered = [p for p in data["positions"] if not p["enterable"]]
    assert len(unentered) == 1
    assert unentered[0]["mint"] == MINT_C


@pytest.mark.django_db
def test_unentered_position_has_null_entry_exit(banked_sandbox):
    """Un-enterable positions must have null entry_ts/entry_price/exit_ts/exit_price.

    exit_ts is null for un-enterable rows because entry_ts is null (no trade fired).
    """
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    unentered = next(p for p in data["positions"] if not p["enterable"])
    assert unentered["entry_ts"] is None
    assert unentered["entry_price"] is None
    # exit_ts is computed from entry_ts + held_s; both are null for un-enterable rows
    assert unentered["exit_ts"] is None
    assert unentered["exit_price"] is None
    assert unentered["unentered_reason"] == "dead"


# ---------------------------------------------------------------------------
# §6 Candle basis from recorded tape
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_candles_by_mint_keys_cover_all_position_mints(banked_sandbox, banked_swaps):
    """candles_by_mint must have a key for every mint in the positions list."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    position_mints = {p["mint"] for p in data["positions"]}
    candle_mints = set(data["candles_by_mint"].keys())
    assert position_mints == candle_mints


@pytest.mark.django_db
def test_candles_present_when_swap_rows_exist(banked_sandbox, banked_swaps):
    """MINT_A and MINT_B have swap rows → candle lists must be non-empty."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    assert len(data["candles_by_mint"][MINT_A]) > 0, "Expected candles for MINT_A"
    assert len(data["candles_by_mint"][MINT_B]) > 0, "Expected candles for MINT_B"


@pytest.mark.django_db
def test_candles_empty_when_no_swap_rows_for_mint(banked_sandbox, banked_swaps):
    """MINT_C has no swap rows → candle list must be empty (not an error)."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    assert data["candles_by_mint"][MINT_C] == []


@pytest.mark.django_db
def test_candle_entries_have_required_fields(banked_sandbox, banked_swaps):
    """Each candle dict must contain t, open, high, low, close, vol, interval_s."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_001})
    data = resp.json()
    # MINT_A has swaps → at least one candle
    candles = data["candles_by_mint"][MINT_A]
    assert len(candles) > 0
    for candle in candles:
        missing = _REQUIRED_CANDLE_FIELDS - set(candle.keys())
        assert not missing, f"Candle missing fields: {missing}"


# ---------------------------------------------------------------------------
# §7 Empty state
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_empty_run_id_no_candles(db):
    """Unknown run_id → candles_by_mint must be an empty dict."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_UNKNOWN})
    data = resp.json()
    assert data["candles_by_mint"] == {}


@pytest.mark.django_db
def test_empty_run_id_zero_total_positions(db):
    """Unknown run_id → total_positions must be 0."""
    client = APIClient()
    resp = client.get(URL, {"run_id": RUN_UNKNOWN})
    data = resp.json()
    assert data["total_positions"] == 0
