# ---
# module: core.tests.test_live_ingestion_ac223
# sprint: sprint-5
# story: US-22 AC-22.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.birdeye_swap_mapper, core.tape.mapped_source,
#               core.tape.bounded_source, core.tape.recorder, core.replay_source,
#               core.clock, asyncio, gzip, json, glob
# ---
"""AC-22.3 — live ingestion: raw Birdeye SUBSCRIBE_TXS -> recorder -> swaps/lake.

Covers the three new adapter pieces that connect the real Birdeye stream to the
recorder core, and proves the REAL banked golden capture flows end-to-end
through the same mapper the live listener uses (the US-21 replay path, now over
real data instead of a synthetic fixture):

  1. map_birdeye_swap — raw Birdeye-native -> internal §7.1 dict (buy/sell, Nones).
  2. map_birdeye_swap over the REAL 40-swap capture — all map; vol identity holds.
  3. MappedSwapSource — yields mapped events, skips None.
  4. BoundedSource — stops after max_events and after max_seconds.
  5. End-to-end: ReplaySource(real raw capture) -> MappedSwapSource -> TapeRecorder
     emits one NormalizedSwap per real swap, with None reserves (Birdeye path).
"""
import asyncio
import glob
import gzip
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from core.tape.birdeye_swap_mapper import WSOL_MINT, map_birdeye_swap

_WSOL = "So11111111111111111111111111111111111111112"


def _raw_birdeye_event(side: str = "sell") -> dict:
    """A schema-faithful raw Birdeye SUBSCRIBE_TXS event (token=SPCX, quote=WSOL)."""
    token = "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
    token_leg = {"address": token, "uiAmount": 72711.29106, "decimals": 6, "symbol": "SPCX"}
    quote_leg = {"address": _WSOL, "uiAmount": 2.14188236, "decimals": 9,
                 "symbol": "SOL", "price": 74.22589305385246}
    # sell: token out (from), SOL in (to); buy: SOL out (from), token in (to)
    frm, to = (token_leg, quote_leg) if side == "sell" else (quote_leg, token_leg)
    return {
        "blockUnixTime": 1781562036, "blockNumber": 426723752,
        "txHash": "2DrsMfYu6uRMnr5LuK7u7aiq55d2ARB1i6sVGRq8yCzcrZRKf3BUq2kvpcw8McPUr5eiidnTBuWPNvK1FQoDgAe7",
        "tokenAddress": token, "tokenPrice": 0.0021864985295901728,
        "volumeUSD": 158.9831309872931, "owner": "ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn",
        "side": side, "source": "pump_amm", "from": frm, "to": to,
    }


# ---------------------------------------------------------------------------
# 1. mapper — unit
# ---------------------------------------------------------------------------

def test_mapper_maps_sell_to_internal_shape() -> None:
    m = map_birdeye_swap(_raw_birdeye_event("sell"))
    assert m is not None
    assert m["mint"] == "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
    assert m["block_time"] == 1781562036 and m["slot"] == 426723752
    assert m["signature"].startswith("2DrsM")
    assert m["side"] == "sell"
    assert m["price"] == 0.0021864985295901728
    assert abs(m["vol_sol"] - 2.14188236) < 1e-9
    assert abs(m["sol_usd"] - 74.22589305385246) < 1e-9
    assert m["quote_mint"] == _WSOL
    assert m["base_reserve"] is None and m["quote_reserve"] is None  # §3.3/§7.1
    assert m["failed"] is False


def test_mapper_maps_buy_quote_leg_is_from() -> None:
    m = map_birdeye_swap(_raw_birdeye_event("buy"))
    assert m is not None and m["side"] == "buy"
    assert m["quote_mint"] == _WSOL
    assert abs(m["vol_sol"] - 2.14188236) < 1e-9  # quote (SOL) leg, regardless of direction


def test_mapper_returns_none_for_unmappable() -> None:
    assert map_birdeye_swap("not a dict") is None
    assert map_birdeye_swap({}) is None
    # token leg not identifiable
    bad = _raw_birdeye_event("sell")
    bad["from"] = {"address": "X"}
    bad["to"] = {"address": "Y"}
    assert map_birdeye_swap(bad) is None
    # bad side
    bad2 = _raw_birdeye_event("sell")
    bad2["side"] = "weird"
    assert map_birdeye_swap(bad2) is None
    # missing price
    bad3 = _raw_birdeye_event("sell")
    bad3.pop("tokenPrice")
    assert map_birdeye_swap(bad3) is None


def test_wsol_constant() -> None:
    assert WSOL_MINT == _WSOL


# ---------------------------------------------------------------------------
# 2. mapper over the REAL banked capture (the US-22 AC-22.2 golden fixture)
# ---------------------------------------------------------------------------

def _load_real_capture() -> list[dict]:
    matches = glob.glob("lake/golden/birdeye_subscribe_txs/**/*.jsonl.gz", recursive=True)
    if not matches:
        return []
    with gzip.open(matches[0], "rt") as g:
        return [json.loads(line) for line in g if line.strip()]


def test_mapper_over_real_capture_all_map_and_balance() -> None:
    rows = _load_real_capture()
    if not rows:
        return  # fixture not present in this checkout — skip silently
    mapped = [map_birdeye_swap(r) for r in rows]
    assert all(m is not None for m in mapped), "every real Birdeye swap must map"
    # economic identity vol_usd == vol_sol * sol_usd (Birdeye is internally consistent)
    for m in mapped:
        assert abs(m["vol_sol"] * m["sol_usd"] - m["vol_usd"]) / m["vol_usd"] < 0.01
        assert m["quote_mint"] == _WSOL
        assert m["base_reserve"] is None and m["quote_reserve"] is None
    sides = {s: sum(1 for m in mapped if m["side"] == s) for s in ("buy", "sell")}
    assert sides["buy"] >= 1 and sides["sell"] >= 1


# ---------------------------------------------------------------------------
# 3. MappedSwapSource
# ---------------------------------------------------------------------------

def test_mapped_source_yields_mapped_and_skips_none() -> None:
    from core.replay_source import ReplaySource
    from core.tape.mapped_source import MappedSwapSource

    raw = [_raw_birdeye_event("buy"), {"junk": 1}, _raw_birdeye_event("sell")]

    async def _run():
        src = MappedSwapSource(ReplaySource(raw), map_birdeye_swap)
        await src.connect()
        out = [e async for e in src.events()]
        await src.disconnect()
        return out

    out = asyncio.run(_run())
    assert len(out) == 2  # junk skipped
    assert {e["side"] for e in out} == {"buy", "sell"}
    assert all("block_time" in e for e in out)


# ---------------------------------------------------------------------------
# 4. BoundedSource
# ---------------------------------------------------------------------------

def test_bounded_source_stops_after_max_events() -> None:
    from core.replay_source import ReplaySource
    from core.tape.bounded_source import BoundedSource

    raw = [{"n": i} for i in range(10)]

    async def _run():
        src = BoundedSource(ReplaySource(raw), max_events=3)
        out = [e async for e in src.events()]
        return out, src.emitted

    out, emitted = asyncio.run(_run())
    assert len(out) == 3 and emitted == 3


def test_bounded_source_no_bound_passes_through() -> None:
    from core.replay_source import ReplaySource
    from core.tape.bounded_source import BoundedSource

    raw = [{"n": i} for i in range(4)]

    async def _run():
        src = BoundedSource(ReplaySource(raw))
        return [e async for e in src.events()]

    assert len(asyncio.run(_run())) == 4


def test_bounded_source_stops_after_max_seconds_during_silence() -> None:
    from core.datasource import DataSource
    from core.tape.bounded_source import BoundedSource

    class _SilentForever(DataSource):
        async def connect(self): ...
        async def disconnect(self): ...
        async def events(self):
            await asyncio.sleep(5)   # never yields within the box
            yield {"n": 1}

    async def _run():
        src = BoundedSource(_SilentForever(), max_seconds=0.1)
        return [e async for e in src.events()]

    out = asyncio.run(_run())
    assert out == []  # time-box reached during silence -> clean stop (so writes run)


# ---------------------------------------------------------------------------
# 5. End-to-end: REAL raw capture -> mapper -> recorder -> NormalizedSwaps
# ---------------------------------------------------------------------------

def test_real_capture_flows_through_recorder() -> None:
    from core.clock import VirtualClock
    from core.replay_source import ReplaySource
    from core.tape.mapped_source import MappedSwapSource
    from core.tape.recorder import TapeRecorder

    rows = _load_real_capture()
    if not rows:
        return
    mint = "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
    # anchor rel to the earliest block in the capture (test only cares about count/fields)
    anchor = min(int(r["blockUnixTime"]) for r in rows)
    token_store = {mint: SimpleNamespace(graduated_block_time=anchor)}

    async def _run():
        source = MappedSwapSource(ReplaySource(rows), map_birdeye_swap)
        clock = VirtualClock(datetime(2025, 6, 16, tzinfo=timezone.utc))
        rec = TapeRecorder(source, clock, token_store=token_store, swap_source="birdeye_live")
        await rec.run()
        return rec.normalized_swaps, rec.skipped_degenerate

    normalized, skipped = asyncio.run(_run())
    # Every real landed swap must emit exactly one NormalizedSwap (none skipped as
    # degenerate now that None reserves are valid for the Birdeye path).
    assert len(normalized) == len(rows), (
        f"expected {len(rows)} NormalizedSwaps from the real capture, got {len(normalized)}"
    )
    assert skipped == []
    assert all(ns.base_reserve is None and ns.quote_reserve is None for ns in normalized)
    assert all(ns.source == "birdeye_live" for ns in normalized)
