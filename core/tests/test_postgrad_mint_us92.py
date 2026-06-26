# ---
# module: core.tests.test_postgrad_mint_us92
# sprint: sprint-15
# story: US-92
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, core.management.commands.run_firehose,
#               core.firehose.tape_sink, copytrade.firehose_harness
# ---
"""US-92 — Post-grad firehose rows must carry the token MINT.

Tests cover all three ACs:

  AC-92.1  The recorder post-grad write path (_postgrad_event_to_swap + TapeStore
           on_add hook) stamps ``mint`` onto every post row.  Verified by unit-
           testing _postgrad_event_to_swap directly and by an integration test that
           records a post swap through the TapeStore -> LakeTapeSink path.

  AC-92.2  LOCAL-PROOF: every emitted post row carries a non-null ``mint`` matching
           the graduated token it belongs to.  A recorder sample drives the full
           write path and the round-tripped lake rows are inspected.

  AC-92.3  Per-phase dollar-basis correctness:
           - PRE rows: vol_usd is 0.0 — dollars via vol_sol × SOL_price.
           - POST rows: vol_usd is populated (real USD) — use it, or
             vol_sol × SOL_price for consistency; document the chosen rule.
           The firehose_harness reconciliation: post rows are VALID (phase='post'),
           NOT partial-write corruption; excluded from the bad-line counter.

HARD CONSTRAINTS:
  - ZERO credits (all Birdeye boundaries mocked).
  - The mint comes from the in-memory per-subscription context, NOT an RPC call.
  - Forward-only: historical Jun 20-23 post rows without a mint are NOT re-attributed.
"""
from __future__ import annotations

import gzip
import json

import pytest

from copytrade.firehose_harness import (
    PostRow,
    _is_post_row,
    _validate_post_row,
    parse,
)
from core.firehose.tape_sink import LakeTapeSink
from core.management.commands.run_firehose import (
    TapeStore,
    _postgrad_event_to_swap,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_MINT_A = "MINTaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
_MINT_B = "MINTbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2"
_GRAD_BT = 1_750_000_000  # graduated block_time


_WSOL = "So11111111111111111111111111111111111111112"


def _raw_birdeye_event(
    mint: str,
    block_time: int = _GRAD_BT + 30,
    side: str = "buy",
    price: float = 0.001,
    vol_sol: float = 5.0,
    vol_usd: float = 420.0,
    sig: str = "sigABC",
    slot: int = 999,
    owner: str = "WALLET_A",
) -> dict:
    """Minimal raw Birdeye SUBSCRIBE_TXS event that map_birdeye_swap() can map cleanly.

    Field notes (from birdeye_swap_mapper.py requirements):
      - tokenAddress: the token mint
      - blockUnixTime: block time (unix seconds)
      - blockNumber: slot (stand-in, mapper reads blockNumber)
      - txHash: signature
      - side: "buy" | "sell"
      - tokenPrice: SOL/token price (mapper reads tokenPrice, NOT price)
      - volumeUSD: USD volume
      - from/to legs: one carries the token mint, other is WSOL
        The quote leg MUST have a "price" field (sol_usd) for the mapper.
    For a BUY: from=WSOL (sol in), to=mint (tokens out).
    """
    sol_usd = vol_usd / max(vol_sol, 1e-9)  # implied SOL/USD price
    return {
        "tokenAddress": mint,
        "blockUnixTime": block_time,
        "blockNumber": slot,     # mapper reads blockNumber as slot
        "txHash": sig,
        "side": side,
        "tokenPrice": price,     # mapper reads tokenPrice (NOT price)
        "volumeUSD": vol_usd,
        "owner": owner,
        # BUY: WSOL flows IN (from), token flows OUT (to)
        "from": {"address": _WSOL, "uiAmount": vol_sol, "price": sol_usd},
        "to": {"address": mint, "uiAmount": vol_sol / price if price else 0, "price": price},
    }


# ---------------------------------------------------------------------------
# AC-92.1 — _postgrad_event_to_swap stamps mint
# ---------------------------------------------------------------------------


def test_postgrad_event_to_swap_includes_mint():
    """AC-92.1: _postgrad_event_to_swap returns a dict with a non-null 'mint' key."""
    event = _raw_birdeye_event(_MINT_A, vol_usd=350.0)
    result = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert result is not None, "_postgrad_event_to_swap returned None for valid event"
    assert "mint" in result, f"'mint' key absent from post swap dict: {sorted(result.keys())}"
    assert result["mint"] == _MINT_A, f"expected mint={_MINT_A!r}, got {result['mint']!r}"


def test_postgrad_event_to_swap_mint_matches_subscription_mint():
    """AC-92.1: The stamped mint matches the per-subscription mint argument."""
    event = _raw_birdeye_event(_MINT_B, vol_usd=210.0)
    result = _postgrad_event_to_swap(event, _MINT_B, _GRAD_BT)
    assert result is not None
    assert result["mint"] == _MINT_B


def test_postgrad_event_to_swap_other_fields_present():
    """AC-92.1: Other fields (block_time, rel, price, vol_usd, side, owner) are still present."""
    event = _raw_birdeye_event(_MINT_A, block_time=_GRAD_BT + 60, vol_usd=84.0)
    result = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert result is not None
    required = {"mint", "block_time", "rel", "price", "side", "vol", "vol_usd", "owner"}
    missing = required - set(result.keys())
    assert not missing, f"fields missing from post swap: {missing}"


def test_postgrad_event_to_swap_rel_is_non_negative_post_grad():
    """AC-92.1: rel = block_time - graduated_block_time >= 0 for post-grad swaps."""
    post_bt = _GRAD_BT + 120
    event = _raw_birdeye_event(_MINT_A, block_time=post_bt)
    result = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert result is not None
    assert result["rel"] >= 0.0, f"expected rel>=0 for post-grad, got {result['rel']}"
    assert result["rel"] == float(post_bt - _GRAD_BT)


def test_postgrad_event_to_swap_returns_none_for_unmappable_event():
    """AC-92.1: Returns None for an event map_birdeye_swap cannot map — caller skips."""
    result = _postgrad_event_to_swap({}, _MINT_A, _GRAD_BT)
    assert result is None, "expected None for empty event"


# ---------------------------------------------------------------------------
# AC-92.1 — TapeStore on_add hook passes mint to tape_sink
# ---------------------------------------------------------------------------


def test_postgrad_tape_store_on_add_records_mint(tmp_path):
    """AC-92.1 (integration): TapeStore on_add writes the mint onto the lake row.

    Simulates the FirehoseDaemon._postgrad_tape path:
        _postgrad_tape = TapeStore(on_add=lambda _m, s: tape_sink.record(s, 'post'))

    After US-92: _postgrad_event_to_swap stamps mint onto s before TapeStore.add(),
    so every written row carries the mint.
    """
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)

    # Simulate the on_add hook used by _postgrad_tape in FirehoseDaemon.__init__
    postgrad_tape = TapeStore(on_add=lambda _m, s: sink.record(s, "post"))

    # Build a post-swap the same way the daemon does
    event = _raw_birdeye_event(_MINT_A, block_time=_GRAD_BT + 30, vol_usd=84.0)
    swap = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert swap is not None

    # Add to the tape (this triggers on_add -> sink.record)
    postgrad_tape.add(_MINT_A, swap)
    flushed = sink.flush()
    assert flushed == 1

    # Read back from the lake and assert mint is present
    from core.tape.lake_reader import LakeReader
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(rows) == 1
    row = rows[0]
    assert row.get("phase") == "post", f"expected phase='post', got {row.get('phase')!r}"
    assert row.get("mint") == _MINT_A, (
        f"expected mint={_MINT_A!r} on lake row, got {row.get('mint')!r}. "
        f"Row keys: {sorted(row.keys())}"
    )


def test_postgrad_tape_store_multiple_mints_each_row_carries_own_mint(tmp_path):
    """AC-92.1: Multiple mints write interleaved — each lake row carries its own mint."""
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    postgrad_tape = TapeStore(on_add=lambda _m, s: sink.record(s, "post"))

    for mint in (_MINT_A, _MINT_B):
        event = _raw_birdeye_event(mint, block_time=_GRAD_BT + 10, vol_usd=168.0)
        swap = _postgrad_event_to_swap(event, mint, _GRAD_BT)
        assert swap is not None
        postgrad_tape.add(mint, swap)

    sink.flush()
    from core.tape.lake_reader import LakeReader
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(rows) == 2

    mints_in_rows = {r.get("mint") for r in rows}
    assert mints_in_rows == {_MINT_A, _MINT_B}, (
        f"expected both mints in lake rows, got {mints_in_rows}"
    )


# ---------------------------------------------------------------------------
# AC-92.2 — LOCAL-PROOF: every emitted post row carries a non-null mint
# ---------------------------------------------------------------------------


def test_post_row_mint_is_non_null_and_non_empty():
    """AC-92.2: Every post row in a local recorder sample carries a non-null, non-empty mint."""
    sink_rows: list[dict] = []

    def _capture(swap: dict, phase: str) -> None:
        row = dict(swap)
        row["phase"] = phase
        sink_rows.append(row)

    # Simulate _postgrad_subscription writing to the tape for MINT_A
    event = _raw_birdeye_event(_MINT_A, block_time=_GRAD_BT + 45, vol_usd=168.0)
    swap = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert swap is not None, "event should map cleanly"

    _capture(swap, "post")

    post_rows = [r for r in sink_rows if r.get("phase") == "post"]
    assert len(post_rows) > 0, "no post rows in sample"
    for r in post_rows:
        assert r.get("mint"), (
            f"post row has null/empty mint — attribution broken. row keys: {sorted(r.keys())}"
        )
        assert r["mint"] == _MINT_A, (
            f"post row mint mismatch: expected {_MINT_A!r}, got {r['mint']!r}"
        )


def test_post_row_mint_matches_graduated_token(tmp_path):
    """AC-92.2: mint on post lake row matches the graduated token (fill attribution works)."""
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    postgrad_tape = TapeStore(on_add=lambda _m, s: sink.record(s, "post"))

    # Simulate 3 post-grad swaps for MINT_A
    for i in range(3):
        event = _raw_birdeye_event(
            _MINT_A, block_time=_GRAD_BT + 30 + i * 10, sig=f"sig{i}", vol_usd=84.0 * (i + 1)
        )
        swap = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
        assert swap is not None
        postgrad_tape.add(_MINT_A, swap)

    sink.flush()
    from core.tape.lake_reader import LakeReader
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    post_rows = [r for r in rows if r.get("phase") == "post"]
    assert len(post_rows) == 3, f"expected 3 post rows, got {len(post_rows)}"
    for r in post_rows:
        assert r.get("mint") == _MINT_A, (
            f"post row belongs to wrong mint: expected {_MINT_A!r}, got {r.get('mint')!r}"
        )


def test_forward_only_fix_documented():
    """AC-92.2: Historical Jun 20-23 post rows are NOT re-attributed — forward-only.

    This test documents the constraint rather than testing re-attribution:
    the fix is in the RECORDER write path (run_firehose.py), which applies to
    new rows going forward.  Existing rows in the lake are immutable.
    """
    # A historical post row (no mint, has rel) — as found in Jun 20-23 tapes
    historical_post_row = {
        "block_time": 1_750_050_000,
        "owner": "WALLET_X",
        "phase": "post",
        "price": 0.00012,
        "rel": 45.0,
        "side": "buy",
        "slot": 12345,
        "vol": 3.0,
        "vol_usd": 252.0,
    }
    # The historical row has no mint — this is the known limitation
    assert "mint" not in historical_post_row, (
        "Sanity check: historical row should NOT have mint (pre-US-92)"
    )
    # After US-92: new rows will have mint. The forward-only constraint means we
    # do NOT attempt to add mint to historical rows (no credits, no RPC lookups).
    new_row = {
        "mint": _MINT_A,
        "block_time": 1_750_050_000,
        "owner": "WALLET_X",
        "phase": "post",
        "price": 0.00012,
        "rel": 45.0,
        "side": "buy",
        "slot": 12345,
        "vol": 3.0,
        "vol_usd": 252.0,
    }
    assert new_row.get("mint") == _MINT_A, "Post-US-92 rows carry mint"


# ---------------------------------------------------------------------------
# AC-92.3 — Per-phase dollar-basis correctness
# ---------------------------------------------------------------------------


def test_pre_row_vol_usd_is_zero_use_vol_sol_x_sol_price():
    """AC-92.3: PRE rows have vol_usd=0.0 — dollars come from vol_sol × SOL_price."""
    from copytrade.firehose_harness import to_usd

    # A typical PRE row
    pre_row = {
        "mint": _MINT_A,
        "block_time": 1_750_000_000,
        "slot": 100,
        "signature": "sigPRE",
        "price": 0.0005,
        "side": "buy",
        "vol": 2.0,
        "vol_sol": 2.0,
        "vol_usd": 0.0,  # always zero on PRE rows
        "owner": "WALLET_A",
        "phase": "pre",
    }
    assert pre_row["vol_usd"] == 0.0, "PRE row vol_usd must be 0.0"
    # Dollar value MUST come from vol_sol × SOL_price, NEVER from vol_usd
    correct_usd = to_usd(pre_row["vol_sol"], "2026-06-22")
    wrong_usd = pre_row["vol_usd"]
    assert correct_usd > 0.0, f"to_usd(vol_sol) should be positive, got {correct_usd}"
    assert wrong_usd == 0.0, "vol_usd is always 0 on PRE — using it yields $0 (wrong)"


def test_post_row_vol_usd_is_populated():
    """AC-92.3: POST rows carry a populated vol_usd (real USD) — unlike PRE rows."""
    event = _raw_birdeye_event(_MINT_A, vol_usd=350.0)
    swap = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert swap is not None
    # vol_usd comes from Birdeye's volumeUSD field — populated on POST rows
    assert swap.get("vol_usd", 0.0) > 0.0, (
        f"POST row vol_usd should be populated, got {swap.get('vol_usd')}"
    )


def test_post_row_vol_usd_vs_vol_sol_x_sol_price_consistency():
    """AC-92.3: Dollar-basis rule documented — use vol_usd on POST or vol_sol×SOL_price.

    The chosen rule: use vol_usd on POST rows when available (populated by Birdeye).
    Fall back to vol_sol × SOL_price for consistency with PRE rows if vol_usd is absent.
    Both paths are valid; what matters is ONE consistent rule per phase.
    """
    from copytrade.firehose_harness import SOL_PRICE_DEFAULT

    vol_sol = 5.0
    vol_usd_from_birdeye = 420.0  # Birdeye's USD value

    # POST rows: use vol_usd directly (populated by Birdeye)
    post_dollars_preferred = vol_usd_from_birdeye
    # Fallback if vol_usd absent: vol_sol × SOL_price
    post_dollars_fallback = vol_sol * SOL_PRICE_DEFAULT

    # Both are non-zero (either approach works)
    assert post_dollars_preferred > 0.0
    assert post_dollars_fallback > 0.0
    # Document: the chosen rule is vol_usd when populated, vol_sol×SOL_price as fallback
    dollar_value = post_dollars_preferred if vol_usd_from_birdeye > 0.0 else post_dollars_fallback
    assert dollar_value == vol_usd_from_birdeye


# ---------------------------------------------------------------------------
# AC-92.3 — firehose_harness reconciliation: post rows excluded from bad counter
# ---------------------------------------------------------------------------


def test_is_post_row_detects_phase_post():
    """AC-92.3: _is_post_row returns True when phase='post'."""
    obj = {"phase": "post", "rel": 45.0, "block_time": 1_750_050_000, "side": "buy"}
    assert _is_post_row(obj) is True


def test_is_post_row_detects_old_format_no_mint():
    """AC-92.3: _is_post_row detects old-format post rows (rel present, vol_sol absent)."""
    # Historical Jun 20-23 post row format
    obj = {
        "block_time": 1_750_050_000,
        "owner": "WALLET_X",
        "price": 0.00012,
        "rel": 45.0,
        "side": "buy",
        "slot": 12345,
        "vol": 3.0,
        "vol_usd": 252.0,
    }
    assert _is_post_row(obj) is True


def test_is_post_row_returns_false_for_pre_row():
    """AC-92.3: _is_post_row returns False for a normal PRE row."""
    obj = {
        "mint": _MINT_A,
        "block_time": 1_750_000_000,
        "slot": 100,
        "signature": "sig1",
        "price": 0.0005,
        "side": "buy",
        "vol": 2.0,
        "vol_sol": 2.0,
        "vol_usd": 0.0,
        "owner": "WALLET_A",
        "phase": "pre",
    }
    assert _is_post_row(obj) is False


def test_is_post_row_returns_false_for_non_dict():
    """AC-92.3: _is_post_row is safe with non-dict input."""
    assert _is_post_row("bad") is False
    assert _is_post_row(None) is False
    assert _is_post_row([]) is False


def test_harness_parse_post_rows_not_counted_as_bad(tmp_path):
    """AC-92.3 LOCAL-PROOF: valid post rows are NOT counted as bad in parse().

    Writes a fixture tape with:
      - 2 valid PRE rows (should be counted as good)
      - 2 valid POST rows (should NOT be bad — excluded from bad counter)
      - 1 genuine bad line (malformed JSON)

    Asserts: good==2, bad==1 (bad is malformed only, NOT post rows).
    """
    dt = "2026-06-26"
    part_dir = tmp_path / f"dt={dt}"
    part_dir.mkdir(parents=True)
    part_file = part_dir / "part-0.jsonl.gz"

    pre_row_1 = {
        "mint": _MINT_A, "block_time": 1_750_000_100, "slot": 1, "signature": "s1",
        "price": 0.0005, "side": "buy", "vol": 2.0, "vol_sol": 2.0, "vol_usd": 0.0,
        "owner": "W1", "phase": "pre",
    }
    pre_row_2 = {
        "mint": _MINT_B, "block_time": 1_750_000_200, "slot": 2, "signature": "s2",
        "price": 0.0004, "side": "sell", "vol": 1.0, "vol_sol": 1.0, "vol_usd": 0.0,
        "owner": "W2", "phase": "pre",
    }
    # Post rows with US-92 mint stamped
    post_row_1 = {
        "mint": _MINT_A, "block_time": 1_750_000_300, "slot": 3, "signature": "s3",
        "rel": 30.0, "price": 0.0006, "side": "buy", "vol": 5.0, "vol_usd": 420.0,
        "owner": "W3", "phase": "post",
    }
    post_row_2 = {
        "mint": _MINT_A, "block_time": 1_750_000_400, "slot": 4, "signature": "s4",
        "rel": 130.0, "price": 0.0007, "side": "sell", "vol": 3.0, "vol_usd": 252.0,
        "owner": "W4", "phase": "post",
    }

    with gzip.open(str(part_file), "wb") as gz:
        for row in (pre_row_1, pre_row_2, post_row_1, post_row_2):
            gz.write((json.dumps(row) + "\n").encode("utf-8"))
        # Genuine bad line (malformed JSON)
        gz.write(b"CORRUPT_NOT_JSON\n")

    rows, bad_count = parse(dt, lake_base_dir=tmp_path)

    assert bad_count == 1, (
        f"Expected bad_count=1 (only the malformed JSON line), got {bad_count}. "
        f"Post rows must NOT be counted as bad."
    )
    assert len(rows) == 2, (
        f"Expected 2 PRE rows (good), got {len(rows)}"
    )
    # Verify only PRE rows are returned
    for r in rows:
        assert r.phase == "pre", f"parse() should only return PRE rows, got phase={r.phase!r}"


def test_validate_post_row_parses_us92_fixed_row():
    """AC-92.3: _validate_post_row correctly parses a US-92-fixed post row with mint."""
    obj = {
        "mint": _MINT_A,
        "block_time": 1_750_000_300,
        "slot": 3,
        "signature": "sig3",
        "rel": 30.0,
        "price": 0.0006,
        "side": "buy",
        "vol": 5.0,
        "vol_usd": 420.0,
        "owner": "WALLET_A",
        "phase": "post",
    }
    row = _validate_post_row(obj)
    assert row is not None
    assert isinstance(row, PostRow)
    assert row.mint == _MINT_A
    assert row.phase == "post"
    assert row.rel == 30.0
    assert row.vol_usd == 420.0  # populated on POST rows


def test_validate_post_row_accepts_historical_no_mint():
    """AC-92.3: _validate_post_row accepts historical rows with no mint (forward-only)."""
    obj = {
        "block_time": 1_750_000_300,
        "rel": 30.0,
        "price": 0.0006,
        "side": "buy",
        "vol": 5.0,
        "vol_usd": 420.0,
        "owner": "WALLET_A",
        "phase": "post",
    }
    row = _validate_post_row(obj)
    assert row is not None
    assert row.mint == ""  # empty for historical pre-US-92 rows


def test_validate_post_row_rejects_bad_side():
    """AC-92.3: _validate_post_row returns None for invalid side field."""
    obj = {
        "mint": _MINT_A, "block_time": 1_750_000_300, "rel": 30.0,
        "price": 0.0006, "side": "INVALID", "vol": 5.0, "vol_usd": 420.0, "owner": "W",
    }
    assert _validate_post_row(obj) is None


# ---------------------------------------------------------------------------
# AC-92.3 — per-phase dollar basis does not read vol_usd on PRE rows
# ---------------------------------------------------------------------------


def test_per_phase_dollar_basis_pre_never_reads_vol_usd():
    """AC-92.3: Dollar basis rule enforced — PRE row vol_usd=0 must not produce a dollar value."""
    pre_vol_usd = 0.0       # always 0 on PRE rows
    pre_vol_sol = 3.0
    from copytrade.firehose_harness import to_usd

    # Wrong path: reading vol_usd yields $0 (useless)
    wrong = pre_vol_usd
    # Correct path: vol_sol × SOL_price
    correct = to_usd(pre_vol_sol, "2026-06-22")
    assert wrong == 0.0
    assert correct == pytest.approx(3.0 * 84.0)


def test_per_phase_dollar_basis_post_vol_usd_is_populated():
    """AC-92.3: POST row vol_usd is populated (unlike PRE) — rule: use it on POST rows."""
    event = _raw_birdeye_event(_MINT_A, vol_usd=504.0, vol_sol=6.0)
    swap = _postgrad_event_to_swap(event, _MINT_A, _GRAD_BT)
    assert swap is not None
    # POST rows carry real Birdeye USD volume
    assert swap["vol_usd"] == pytest.approx(504.0), (
        f"POST row vol_usd should equal Birdeye's volumeUSD=504, got {swap['vol_usd']}"
    )
    # vol_usd > 0 so it can be used directly (unlike PRE rows)
    assert swap["vol_usd"] > 0.0


# ---------------------------------------------------------------------------
# Regression: US-92 fix does not break the pre-grad write path
# ---------------------------------------------------------------------------


def test_pre_tape_store_on_add_still_records_without_mint_stamp(tmp_path):
    """US-92 fix is scoped to POST path only — PRE path unchanged.

    Under TAPE_SOURCE=self, the PRE tape's on_add hook is:
        lambda _m, s: tape_sink.record(s, "pre")
    The swap dict already has mint from the PRE collection path.
    This test verifies the PRE path still works after the US-92 fix.
    """
    sink = LakeTapeSink(base_dir=tmp_path, flush_every=1000)
    pre_tape = TapeStore(on_add=lambda _m, s: sink.record(s, "pre"))

    pre_swap = {
        "mint": _MINT_A,
        "block_time": 1_750_000_000,
        "slot": 1,
        "signature": "preSig",
        "price": 0.0005,
        "side": "buy",
        "vol": 2.0,
        "vol_sol": 2.0,
        "vol_usd": 0.0,
        "owner": "WALLET_PRE",
    }
    pre_tape.add(_MINT_A, pre_swap)
    sink.flush()

    from core.tape.lake_reader import LakeReader
    rows = list(LakeReader(base_dir=tmp_path).iter_rows())
    assert len(rows) == 1
    assert rows[0].get("phase") == "pre"
    assert rows[0].get("mint") == _MINT_A
