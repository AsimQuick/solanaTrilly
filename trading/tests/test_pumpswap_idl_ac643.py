# ---
# module: trading.tests.test_pumpswap_idl_ac643
# sprint: sprint-13
# story: US-64 AC-64.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: json, hashlib, pathlib
# ---
"""AC-64.3 — vendored PumpSwap IDL pin + discriminator/account-count assertions.

Verifies:
  1. Vendor artefacts present — pump_amm.json and pin_manifest.json exist at vendor/idl/
  2. IDL content-hash integrity — sha256 of vendored file matches the pin manifest
  3. Buy instruction accounts — exactly 23 per §10.1 pinned ground truth
  4. Sell instruction accounts — exactly 21 per §10.1 pinned ground truth
  5. Buy discriminator — hex bytes == 66063d1201daebea
  6. Sell discriminator — hex bytes == 33e685a4017f83ad
  7. Pin manifest ground-truth fields — manifest records the §10.1 values exactly

No DB, no network, no firehose — all assertions are offline file reads.

Test list
---------
  test_idl_file_exists
  test_pin_manifest_exists
  test_idl_sha256_matches_manifest
  test_buy_account_count_is_23
  test_sell_account_count_is_21
  test_buy_discriminator_hex
  test_sell_discriminator_hex
  test_pin_manifest_ground_truth_fields
"""
import hashlib
import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]  # trading/tests/file -> trading/ -> repo root
IDL_PATH = REPO_ROOT / "vendor" / "idl" / "pump_amm.json"
MANIFEST_PATH = REPO_ROOT / "vendor" / "idl" / "pin_manifest.json"

# §10.1 pinned ground truth
BUY_ACCOUNT_COUNT = 23
SELL_ACCOUNT_COUNT = 21
BUY_DISCRIMINATOR = "66063d1201daebea"
SELL_DISCRIMINATOR = "33e685a4017f83ad"


def _load_idl():
    return json.loads(IDL_PATH.read_text(encoding="utf-8"))


def _load_manifest():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _instruction_by_name(idl: dict, name: str) -> dict:
    for instr in idl.get("instructions", []):
        if instr.get("name") == name:
            return instr
    raise KeyError(f"Instruction '{name}' not found in IDL")


def _discriminator_hex(instr: dict) -> str:
    return "".join(f"{b:02x}" for b in instr["discriminator"])


# ---------------------------------------------------------------------------
# Tests — artefact presence
# ---------------------------------------------------------------------------


def test_idl_file_exists():
    assert IDL_PATH.is_file(), f"pump_amm.json not found at {IDL_PATH}"


def test_pin_manifest_exists():
    assert MANIFEST_PATH.is_file(), f"pin_manifest.json not found at {MANIFEST_PATH}"


# ---------------------------------------------------------------------------
# Tests — content-hash integrity
# ---------------------------------------------------------------------------


def test_idl_sha256_matches_manifest():
    raw = IDL_PATH.read_text(encoding="utf-8")
    actual = hashlib.sha256(raw.encode()).hexdigest()
    manifest = _load_manifest()
    expected = manifest["sha256"]
    assert actual == expected, (
        f"IDL sha256 mismatch — file may have been modified without updating the manifest.\n"
        f"  expected: {expected}\n"
        f"  actual:   {actual}"
    )


# ---------------------------------------------------------------------------
# Tests — account counts (§10.1)
# ---------------------------------------------------------------------------


def test_buy_account_count_is_23():
    idl = _load_idl()
    buy = _instruction_by_name(idl, "buy")
    count = len(buy["accounts"])
    assert count == BUY_ACCOUNT_COUNT, (
        f"Buy instruction must have {BUY_ACCOUNT_COUNT} accounts; got {count}"
    )


def test_sell_account_count_is_21():
    idl = _load_idl()
    sell = _instruction_by_name(idl, "sell")
    count = len(sell["accounts"])
    assert count == SELL_ACCOUNT_COUNT, (
        f"Sell instruction must have {SELL_ACCOUNT_COUNT} accounts; got {count}"
    )


# ---------------------------------------------------------------------------
# Tests — discriminators (§10.1)
# ---------------------------------------------------------------------------


def test_buy_discriminator_hex():
    idl = _load_idl()
    buy = _instruction_by_name(idl, "buy")
    actual = _discriminator_hex(buy)
    assert actual == BUY_DISCRIMINATOR, (
        f"Buy discriminator mismatch: expected {BUY_DISCRIMINATOR}, got {actual}"
    )


def test_sell_discriminator_hex():
    idl = _load_idl()
    sell = _instruction_by_name(idl, "sell")
    actual = _discriminator_hex(sell)
    assert actual == SELL_DISCRIMINATOR, (
        f"Sell discriminator mismatch: expected {SELL_DISCRIMINATOR}, got {actual}"
    )


# ---------------------------------------------------------------------------
# Tests — pin manifest ground-truth fields
# ---------------------------------------------------------------------------


def test_pin_manifest_ground_truth_fields():
    manifest = _load_manifest()
    gt = manifest.get("pinned_ground_truth", {})
    assert gt.get("buy_account_count") == BUY_ACCOUNT_COUNT
    assert gt.get("sell_account_count") == SELL_ACCOUNT_COUNT
    assert gt.get("buy_discriminator") == BUY_DISCRIMINATOR
    assert gt.get("sell_discriminator") == SELL_DISCRIMINATOR
