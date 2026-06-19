# ---
# module: core.tests.test_graduation_detection_integration_hotfix
# sprint: sprint-14
# story: hotfix-graduation-pumpfun-mapping
# status: fixed
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, asyncio, datetime, core.detection.consumer, core.models,
#               core.resolver, core.clock, core.replay_source,
#               core.tape.birdeye_graduation_source
# ---
"""Integration: a real-frame graduation event flows through DetectionConsumer.

End-to-end (offline, deterministic): map the REAL Birdeye pump.fun frame, feed
the emitted MEME_DATA event through DetectionConsumer (via ReplaySource) under
the ACTUAL active detection.filter ({"source":"pump_dot_fun","graduated":True}),
and assert a Token row persists with the right mint / dex_source /
graduated_block_time / null (empty) pool.

This is the bug that broke the live window: frames arrived but ZERO Token rows
persisted because the emitted source did not match the filter and the timestamp
was never mapped.  This test pins the fixed behaviour against the real frame.
"""
import asyncio
from datetime import datetime, timezone

import pytest

from core.clock import VirtualClock
from core.detection.consumer import DetectionConsumer
from core.models import PipelineConfig, Token
from core.replay_source import ReplaySource
from core.resolver import get_active_config, invalidate_active_config_cache
from core.tape.birdeye_graduation_source import (
    DEFAULT_DATA_TYPE,
    DEFAULT_DEX_ALLOWLIST,
    map_new_listing_frame,
)

# Valid sibling sections (same invariants as test_detection_consumer_ac152.py).
VALID_TAPE = {
    "amm_programs": ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"],
    "idle_kill_ttl_s": 1800,
    "reattach": True,
    "birdeye_interval_s": 15,
}
VALID_SCORING = {
    "score_at_elapsed_s": 120,
    "window_s": 180,
    "capture_buffer_s": 4,
    "gate": "adaptive_topk",
}
VALID_OUTCOME = {"window_s": 1800, "label_def": {}}
VALID_TRADING = {
    "gate": "adaptive_topk",
    "enabled": False,
    "position_size_sol": 0.1,
    "max_open_positions": 3,
    "slippage_bps": 50,
}

# The real pump.fun graduation frame (verbatim shape).
_PUMP_FRAME = {
    "type": "TOKEN_NEW_LISTING_DATA",
    "data": {
        "address": "6Y5PBgk9rVC9ycsVTxYi6nFtSytEBrYTPy3mL39Nwokn",
        "decimals": 9,
        "name": "dream",
        "source": "pump_amm",
        "symbol": "dream",
        "liquidity": 18640.85,
        "liquidityAddedAt": "2026-06-19T07:17:33",
    },
}
_PANCAKE_FRAME = {
    "type": "TOKEN_NEW_LISTING_DATA",
    "data": {
        "address": "PancakeMintAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        "source": "pancakeswap_v3",
        "liquidityAddedAt": "2026-06-19T07:18:00",
    },
}
_PUMP_EPOCH = int(datetime(2026, 6, 19, 7, 17, 33, tzinfo=timezone.utc).timestamp())
_T0 = datetime(2026, 6, 19, 12, 0, 0, tzinfo=timezone.utc)


def _make_active_config(source: str = "pump_dot_fun") -> PipelineConfig:
    return PipelineConfig.objects.create(
        version=1,
        label=f"test-config-{source}",
        is_active=True,
        detection={"filter": {"source": source, "graduated": True}},
        tape=VALID_TAPE,
        scoring=VALID_SCORING,
        outcome=VALID_OUTCOME,
        trading=VALID_TRADING,
    )


def _map(frame):
    return map_new_listing_frame(
        frame,
        data_type=DEFAULT_DATA_TYPE,
        event_source="pump_dot_fun",
        dex_allowlist=frozenset(DEFAULT_DEX_ALLOWLIST),
        fallback_epoch=int(_T0.timestamp()),
    )


def _run_consumer(events: list[dict]) -> None:
    async def _run():
        consumer = DetectionConsumer(
            source=ReplaySource(event_log=events),
            clock=VirtualClock(_T0),
            config_fn=get_active_config,
        )
        await consumer.run()

    asyncio.run(_run())


@pytest.mark.django_db(transaction=True)
def test_real_pump_frame_persists_token_row_with_null_pool() -> None:
    """The mapped real frame passes the active filter and persists a Token row."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    event = _map(_PUMP_FRAME)
    assert event is not None
    assert event["poolAddress"] is None  # the frame genuinely has no pool

    _run_consumer([event])

    mint = _PUMP_FRAME["data"]["address"]
    assert Token.objects.filter(mint=mint).exists(), (
        "ZERO-rows regression: the real pump.fun frame must persist a Token row"
    )
    token = Token.objects.get(mint=mint)
    assert token.dex_source == "pump_dot_fun"
    assert token.graduated_block_time == _PUMP_EPOCH
    assert token.graduated_at == datetime.fromtimestamp(_PUMP_EPOCH, tz=timezone.utc)
    # Null pool persisted cleanly (coerced to "" on the non-nullable CharField).
    assert token.pool_address == ""
    # Raw DEX preserved for audit.
    assert token.raw_graduation["dex"] == "pump_amm"
    assert token.raw_graduation["raw"]["source"] == "pump_amm"


@pytest.mark.django_db(transaction=True)
def test_non_pump_frame_yields_nothing_and_persists_no_row() -> None:
    """A pancakeswap_v3 frame is dropped at the source — no event, no Token row."""
    invalidate_active_config_cache()
    _make_active_config(source="pump_dot_fun")

    event = _map(_PANCAKE_FRAME)
    assert event is None  # dropped by the pump.fun allow-list

    # Even if we somehow fed it (defensive), the filter would reject it too.
    _run_consumer([])  # nothing to feed

    assert not Token.objects.filter(
        mint=_PANCAKE_FRAME["data"]["address"]
    ).exists()
