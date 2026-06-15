# ---
# module: core.tests.test_firehose_activation_ac222
# sprint: sprint-5
# story: US-22 AC-22.2.1 AC-22.2.2 AC-22.2.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: pytest, gzip, json, pathlib, re, tools.firehose_activate
# ---
"""AC-22.2 — the single deliberate, time-boxed Birdeye firehose activation.

Verifies the two durable, committed gates of AC-22.2:

  1. The banked golden fixture is REAL captured PumpSwap reality (no synthetic
     rows), complete (every required raw §3.3 field), all for the one mint, all
     `source = pump_amm`, landed, with distinct signatures — committed in the
     repo lake at its declared path.
  2. The ledger entry is committed: the budget table decremented to 9 Birdeye /
     10 Helius and an activation row naming the Birdeye SUBSCRIBE_TXS WS + the
     banked fixture path (PRD §15.7).

Plus unit coverage of the activation tool's pure logic (PumpSwap filter, schema
guard, envelope unwrap, bank/read round-trip, CLI + main control flow).  These
tests are fully OFFLINE — no network, no live Birdeye call.
"""
import gzip
import json
import re
from pathlib import Path

from tools import firehose_activate as fa

# Repo root = three levels up from this test file (core/tests/<file> -> repo).
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LEDGER_PATH = REPO_ROOT / "ops" / "firehose_activation_log.md"
BANKED_MINT = "E6ifp2mJy8cYQehUGUtFvrXriRKxRuonLmrvTFypump"
BANKED_FIXTURE = (
    REPO_ROOT
    / "lake"
    / "golden"
    / "birdeye_subscribe_txs"
    / "dt=2026-06-15"
    / f"{BANKED_MINT}_pumpswap_golden.jsonl.gz"
)


# ---------------------------------------------------------------------------
# Sample raw events (schema-faithful to the real Birdeye SUBSCRIBE_TXS payload)
# ---------------------------------------------------------------------------


def _raw_event(**overrides) -> dict:
    base = {
        "blockUnixTime": 1781562036,
        "owner": "ARu4n5mFdZogZAravu7CcizaojWnS6oqka37gdLT5SZn",
        "source": "pump_amm",
        "txHash": "2DrsMfYu6uRMnr5LuK7u7aiq55d2ARB1i6sVGRq8yCzc",
        "side": "sell",
        "tokenAddress": BANKED_MINT,
        "pricePair": 2.9457e-05,
        "volumeUSD": 158.98,
        "tokenPrice": 0.00218649,
        "blockNumber": 426723752,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Pure-helper unit tests
# ---------------------------------------------------------------------------


def test_is_pumpswap_swap_by_source() -> None:
    assert fa.is_pumpswap_swap(_raw_event(source="pump_amm")) is True


def test_is_pumpswap_swap_by_program_id() -> None:
    ev = _raw_event(source="raydium", interactedProgramId=fa.PUMPSWAP_PROGRAM)
    assert fa.is_pumpswap_swap(ev) is True


def test_is_pumpswap_swap_rejects_other_venue() -> None:
    assert fa.is_pumpswap_swap(_raw_event(source="whirlpool")) is False


def test_is_pumpswap_swap_rejects_non_dict() -> None:
    assert fa.is_pumpswap_swap("not-a-dict") is False


def test_missing_required_fields_complete_event() -> None:
    assert fa.missing_required_fields(_raw_event()) == set()


def test_missing_required_fields_reports_absent() -> None:
    ev = _raw_event()
    del ev["txHash"]
    del ev["tokenPrice"]
    assert fa.missing_required_fields(ev) == {"txHash", "tokenPrice"}


def test_missing_required_fields_non_dict_is_all() -> None:
    assert fa.missing_required_fields(None) == set(fa.REQUIRED_RAW_FIELDS)


def test_is_bankable_swap_accepts_real_pumpswap() -> None:
    assert fa.is_bankable_swap(_raw_event(), BANKED_MINT) is True


def test_is_bankable_swap_rejects_wrong_mint() -> None:
    assert fa.is_bankable_swap(_raw_event(), "someOtherMint") is False


def test_is_bankable_swap_rejects_non_pumpswap() -> None:
    assert fa.is_bankable_swap(_raw_event(source="raydium"), BANKED_MINT) is False


def test_is_bankable_swap_rejects_incomplete() -> None:
    ev = _raw_event()
    del ev["volumeUSD"]
    assert fa.is_bankable_swap(ev, BANKED_MINT) is False


def test_unwrap_envelope_txs_data() -> None:
    inner = _raw_event()
    assert fa.unwrap_envelope({"type": "TXS_DATA", "data": inner}) == inner


def test_unwrap_envelope_welcome_is_none() -> None:
    assert fa.unwrap_envelope({"type": "WELCOME", "data": None}) is None


def test_unwrap_envelope_non_dict_is_none() -> None:
    assert fa.unwrap_envelope("nope") is None


def test_unwrap_envelope_data_not_dict_is_none() -> None:
    assert fa.unwrap_envelope({"type": "TXS_DATA", "data": []}) is None


def test_bank_and_read_round_trip(tmp_path: Path) -> None:
    events = [_raw_event(txHash=f"sig{i}", blockUnixTime=1781562000 + i) for i in range(3)]
    path = fa.bank_fixture(BANKED_MINT, events, date="2026-06-15", base=tmp_path)
    assert path.exists()
    assert path.name == f"{BANKED_MINT}_pumpswap_golden.jsonl.gz"
    assert path.parent.name == "dt=2026-06-15"
    # gzip-compressed
    with open(path, "rb") as fh:
        assert fh.read(2) == b"\x1f\x8b"
    assert fa.read_fixture(path) == events


# ---------------------------------------------------------------------------
# CLI / main() control-flow tests (offline — capture is monkeypatched)
# ---------------------------------------------------------------------------


def test_parse_args_defaults() -> None:
    ns = fa.parse_args(["--mint", BANKED_MINT])
    assert ns.mint == BANKED_MINT
    assert ns.duration_seconds == 300
    assert ns.max_events == 40


def test_main_no_api_key_returns_1(monkeypatch) -> None:
    monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    assert fa.main(["--mint", BANKED_MINT]) == 1


def test_main_empty_capture_banks_nothing(monkeypatch, tmp_path: Path) -> None:
    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(fa, "capture_pumpswap_tape", _empty)
    monkeypatch.setattr(fa, "LAKE_BASE", tmp_path)
    # No synthetic fallback — empty capture returns the dedicated "banked nothing" code.
    assert fa.main(["--mint", BANKED_MINT, "--api-key", "k"]) == 2
    assert list(tmp_path.rglob("*.jsonl.gz")) == []


def test_main_success_banks_real_events(monkeypatch, tmp_path: Path) -> None:
    captured = [_raw_event(txHash=f"sig{i}") for i in range(4)]
    _real_bank = fa.bank_fixture  # capture before patching to avoid recursion

    async def _capture(*a, **k):
        return captured

    def _bank_to_tmp(mint, ev, *, date, **kw):
        return _real_bank(mint, ev, date=date, base=tmp_path)

    monkeypatch.setattr(fa, "capture_pumpswap_tape", _capture)
    monkeypatch.setattr(fa, "bank_fixture", _bank_to_tmp)
    assert fa.main(["--mint", BANKED_MINT, "--api-key", "k"]) == 0
    parts = list(tmp_path.rglob("*.jsonl.gz"))
    assert len(parts) == 1
    assert fa.read_fixture(parts[0]) == captured


# ---------------------------------------------------------------------------
# Committed durable fixture (AC-22.2 gate: banked fixture committed to repo/lake)
# ---------------------------------------------------------------------------


def test_banked_fixture_is_committed() -> None:
    assert BANKED_FIXTURE.exists(), f"banked golden fixture missing at {BANKED_FIXTURE}"


def test_banked_fixture_is_real_complete_pumpswap_tape() -> None:
    rows = fa.read_fixture(BANKED_FIXTURE)
    assert len(rows) >= 1, "fixture must contain at least one real swap"

    signatures = set()
    for i, r in enumerate(rows):
        # real, not a synthetic placeholder
        assert "SYNTHETIC" not in r["txHash"], f"row[{i}] is synthetic"
        # complete §3.3 raw schema
        assert fa.missing_required_fields(r) == set(), f"row[{i}] missing raw fields"
        # PumpSwap (the graduation destination) for the one subscribed mint
        assert r["source"] == fa.PUMPSWAP_SOURCE, f"row[{i}] not pump_amm"
        assert r["tokenAddress"] == BANKED_MINT, f"row[{i}] wrong mint"
        # landed-only — SUBSCRIBE_TXS delivers confirmed swaps; none flagged failed
        assert r.get("failed", False) is False, f"row[{i}] is a failed swap"
        assert r["side"] in {"buy", "sell"}, f"row[{i}] bad side"
        signatures.add(r["txHash"])

    # de-duplicated on signature (no multi-leg double-bank)
    assert len(signatures) == len(rows), "duplicate signatures in fixture"


def test_banked_fixture_is_gzip_jsonl() -> None:
    with open(BANKED_FIXTURE, "rb") as fh:
        assert fh.read(2) == b"\x1f\x8b", "fixture must be gzip-compressed"
    with gzip.open(BANKED_FIXTURE, "rt", encoding="utf-8") as gz:
        for line in gz:
            if line.strip():
                json.loads(line)  # every line is valid JSON


# ---------------------------------------------------------------------------
# Committed ledger entry (AC-22.2 gate: count decremented to 9 Birdeye)
# ---------------------------------------------------------------------------


def test_ledger_budget_decremented_to_9_birdeye() -> None:
    text = LEDGER_PATH.read_text(encoding="utf-8")
    # budget table row: | Birdeye | 10 | 1 | 9 |
    assert re.search(r"\|\s*Birdeye\s*\|\s*10\s*\|\s*1\s*\|\s*9\s*\|", text), (
        "ledger budget table must show Birdeye used=1 remaining=9"
    )


def test_ledger_has_activation_row() -> None:
    text = LEDGER_PATH.read_text(encoding="utf-8")
    assert "Birdeye SUBSCRIBE_TXS" in text
    assert "9 Birdeye / 10 Helius" in text
    # the banked fixture path is referenced in the ledger
    assert f"{BANKED_MINT}_pumpswap_golden.jsonl.gz" in text


# ---------------------------------------------------------------------------
# AC-22.2.1: drives the capture/bank logic against an in-memory stream (NO live network)
# Four assertions: correct dt= path, byte-identical read-back, exclusions, time-box guard
# ---------------------------------------------------------------------------


def _ws_envelope(event: dict) -> str:
    """Wrap a swap dict in a Birdeye TXS_DATA envelope and serialise to JSON string."""
    return json.dumps({"type": "TXS_DATA", "data": event})


def test_is_bankable_swap_rejects_failed_swap() -> None:
    ev = _raw_event(failed=True)
    assert fa.is_bankable_swap(ev, BANKED_MINT) is False


def test_process_stream_writes_to_correct_dt_path_and_reads_back_identical(tmp_path: Path) -> None:
    """Drive capture/bank logic against an in-memory stream; verify path + byte-identical read-back."""
    events = [_raw_event(txHash=f"abc{i}", blockUnixTime=1_700_000_000 + i) for i in range(5)]
    messages = [_ws_envelope(e) for e in events]

    captured = fa._process_message_stream(messages, BANKED_MINT, max_events=10, deadline=1e18)
    assert len(captured) == 5

    date = "2026-06-16"
    path = fa.bank_fixture(BANKED_MINT, captured, date=date, base=tmp_path)

    # correct dt= partition path
    assert path.parent.name == f"dt={date}"
    assert path.name == f"{BANKED_MINT}_pumpswap_golden.jsonl.gz"

    # gzip-compressed
    with open(path, "rb") as fh:
        assert fh.read(2) == b"\x1f\x8b"

    # byte-identical read-back
    assert fa.read_fixture(path) == captured


def test_process_stream_excludes_non_pumpswap_messages(tmp_path: Path) -> None:
    """Non-pump_amm events and WELCOME frames in the stream must be excluded."""
    good = _raw_event(txHash="good1")
    non_amm = _raw_event(txHash="bad1", source="raydium")
    welcome = {"type": "WELCOME", "data": {}}

    messages = [
        _ws_envelope(non_amm),
        json.dumps(welcome),
        _ws_envelope(good),
    ]
    captured = fa._process_message_stream(messages, BANKED_MINT, max_events=10, deadline=1e18)

    assert len(captured) == 1
    assert captured[0]["txHash"] == "good1"


def test_process_stream_excludes_failed_swaps() -> None:
    """Failed swaps in the stream must be excluded (landed-only, §6.2)."""
    good = _raw_event(txHash="landed1")
    failed = _raw_event(txHash="failed1", failed=True)

    messages = [_ws_envelope(failed), _ws_envelope(good)]
    captured = fa._process_message_stream(messages, BANKED_MINT, max_events=10, deadline=1e18)

    assert len(captured) == 1
    assert captured[0]["txHash"] == "landed1"


def test_process_stream_timebox_guard_halts_capture() -> None:
    """The time-box guard must stop capture when the monotonic deadline is reached."""
    messages = [_ws_envelope(_raw_event(txHash=f"t{i}")) for i in range(10)]

    # Fake clock: ticks up by 0.5 per call; deadline=1.0 means iterations 0 and 1
    # pass (0.0 < 1.0, 0.5 < 1.0), iteration 2 trips the guard (1.0 >= 1.0).
    _tick = [0]

    def _fake_mono() -> float:
        v = _tick[0] * 0.5
        _tick[0] += 1
        return v

    captured = fa._process_message_stream(
        messages, BANKED_MINT, max_events=10, deadline=1.0, _monotonic=_fake_mono
    )
    # Time-box halted before all 10 messages were processed
    assert len(captured) < 10
    assert len(captured) == 2  # only iterations 0 and 1 pass the deadline check


def test_process_stream_deduplicates_on_txhash() -> None:
    """Duplicate txHash within the stream must be de-duped (multi-leg route guard)."""
    ev = _raw_event(txHash="dup_sig")
    messages = [_ws_envelope(ev), _ws_envelope(ev), _ws_envelope(ev)]

    captured = fa._process_message_stream(messages, BANKED_MINT, max_events=10, deadline=1e18)
    assert len(captured) == 1
    assert captured[0]["txHash"] == "dup_sig"
