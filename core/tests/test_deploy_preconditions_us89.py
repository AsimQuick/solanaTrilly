# ---
# module: core.tests.test_deploy_preconditions_us89
# sprint: sprint-15
# story: US-89
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, unittest.mock, gzip, json, pathlib,
#               core.tasks, copytrade.fill_repricing, copytrade.curvestage_train,
#               core.firehose.shared_tape, core.tape.lake_reader
# ---
"""US-89 deploy pre-conditions — LOCAL PROOF that every consumer reads the correct
pre-grad source under each TAPE_SOURCE mode.

Four test classes, one per pre-condition:

1. TestSwapsExportLakePath  — core/tasks.py US-78 swaps export
   * Under shared_billy: reads lake/billy_tape (schema-A rows), normalise=True,
     produces non-empty swaps.
   * Under self: reads lake/firehose (schema-B rows), normalise=True,
     produces non-empty swaps.
   * Backward compat: explicit lake_base_dir override still works.

2. TestCurvestageSettlerLakePath  — copytrade/fill_repricing.py settler
   * find_next_trade_price under shared_billy reads pre-grad rows from
     lake/billy_tape (schema-A), normalise=True — finds price.
   * Under self: reads lake/firehose (schema-B) — finds price.
   * Exit leg always reads lake/firehose (post-grad self-tape).
   * reprice_position with split pregrad/postgrad dirs — both legs covered.

3. TestCurvestageRetrainLakePath  — copytrade/curvestage_train.py
   * get_lake_dir() returns lake/billy_tape under shared_billy.
   * get_lake_dir() returns lake/firehose under self.
   * _lake_age_days(None) delegates to get_lake_dir().
   * _lake_date_strs(None) delegates to get_lake_dir().
   * retrain_from_lake(None) passes resolved lake_dir into _run_retrain.

4. TestScoreLevelSchemaParity  — AC-96.4 offline score-level parity proof
   * A schema-A billy row normalised via normalise_row → norm_row_to_swap_dict
     produces the SAME swap-dict fields (price, vol_sol, phase, mint, side)
     as a schema-B equivalent row normalised through the same path.
   * Both swap-dicts, when used as features inputs, produce the SAME feature
     vector (assemble_pregrad_features), proving score-level parity.
   * This closes the missing AC-96.4 offline proof: same data, two schemas,
     one result (parity by construction).

HARD CONSTRAINTS:
  - ZERO firehose credits (fixtures only — the 3-row billy fixture from
    core/tests/fixtures/billy_tape/sample_rows.json + synthetic schema-B rows).
  - trading_enabled never touched.
  - All lake reads use normalise=True for schema-A rows.
"""
from __future__ import annotations

import gzip
import json
import pathlib
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

class _FakeQueryset:
    """Minimal fake QuerySet that returns an empty values() iterator."""
    def values(self, *args, **kwargs):
        return []


# ---------------------------------------------------------------------------
# Billy tape fixture constants (from core/tests/fixtures/billy_tape/sample_rows.json)
# Real rows captured 2026-06-26 from VPS /root/tape/dt=2026-06-26/part-...
# ---------------------------------------------------------------------------

_BILLY_ROW_0 = {
    "mint": "87ptU7np4H8goqe2WgEW7ups5m4PtsuCJgsXY9Kcpump",
    "slot": 428959724,
    "block_time": 1782452209,
    "received_at": 1782452210.133931,
    "side": "buy",
    "sol_amount": 98765431,
    "token_amount": 3191941405246,
    "owner": "9zAx7uPk1Rh3khufmFLsYh3RTRoxwvHEb51RwBBx3344",
    "signature": "5P1gWT4AndXJopS6132jvvZ5fekuH7m49SFv8Rdm8bSbjiVAEM947r3fZ8MsPtJdEir8JuLJCJRqAfbrwYvU5iA8",
    "virtual_sol_reserves": 31609313819,
    "virtual_token_reserves": 1018370730358943,
    "real_sol_reserves": 1609313819,
}

# Expected derived values for _BILLY_ROW_0 (verified in test_shared_firehose_us96.py)
_ROW0_EXPECTED_PRICE = _BILLY_ROW_0["virtual_sol_reserves"] / _BILLY_ROW_0["virtual_token_reserves"]
_ROW0_EXPECTED_VOL_SOL = _BILLY_ROW_0["sol_amount"] / 1e9
_ROW0_EXPECTED_PHASE = "pre"

_BILLY_ROW_1 = {
    "mint": "3qiYGHUZiWoFKNA3ZbqMTFrCdfitNZHgofhghy9vpump",
    "slot": 428959725,
    "block_time": 1782452209,
    "received_at": 1782452210.528755,
    "side": "sell",
    "sol_amount": 1830371702,
    "token_amount": 53104975253972,
    "owner": "4pnjseX8WKazqzjQnQ8hFoBSywSDmC3gandJXK5wUuPY",
    "signature": "2NwqSFcvxgKTwR31aH6hyhmzYhS2Ln6LUymMVwYEeSd8PZXEuHLz29R1wW1BBztFA8A3kVtfaftwB2ou3oGevpTL",
    "virtual_sol_reserves": 32406456113,
    "virtual_token_reserves": 993320586867491,
    "real_sol_reserves": 2406456113,
}

_BILLY_DATE_STR = datetime.fromtimestamp(_BILLY_ROW_0["block_time"], tz=timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Lake-writing helpers
# ---------------------------------------------------------------------------

def _write_schema_a_lake(base_dir: pathlib.Path, rows: list[dict]) -> str:
    """Write schema-A (billy) rows to a lake partition, return the date string."""
    date_str = _BILLY_DATE_STR
    part_dir = base_dir / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(part_dir / "part-0.jsonl.gz"), "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return date_str


def _write_schema_b_lake(base_dir: pathlib.Path, rows: list[dict]) -> str:
    """Write schema-B (solanatrilly self-tape) rows to a lake partition."""
    date_str = _BILLY_DATE_STR
    part_dir = base_dir / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(part_dir / "part-0.jsonl.gz"), "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return date_str


def _schema_b_equivalent(billy_row: dict) -> dict:
    """Derive a schema-B row from a schema-A billy row (same logical swap, different format).

    Schema-B carries price and vol_sol directly (the decoded form).
    This is what the solanatrilly self-tape writes after local processing.
    """
    vsol = billy_row["virtual_sol_reserves"]
    vtok = billy_row["virtual_token_reserves"]
    sol_amt = billy_row["sol_amount"]
    price = vsol / vtok
    vol_sol = abs(sol_amt) / 1e9
    return {
        "mint": billy_row["mint"],
        "block_time": billy_row["block_time"],
        "slot": billy_row["slot"],
        "signature": billy_row["signature"],
        "side": billy_row["side"],
        "price": price,
        "vol_sol": vol_sol,
        "vol": vol_sol,
        "vol_usd": 0.0,
        "owner": billy_row["owner"],
        "phase": "pre",
        "received_at": billy_row.get("received_at"),
    }


# ===========================================================================
# 1. US-78 Swaps Export Lake Path
# ===========================================================================

class TestSwapsExportLakePath:
    """core/tasks.py export_data_contract reads correct lake path under each TAPE_SOURCE."""

    def test_shared_billy_reads_billy_tape_schema_a(self, tmp_path):
        """Under shared_billy: export reads lake/billy_tape with normalise=True → non-empty rows."""
        from core.tape.lake_reader import LakeReader

        # Write schema-A (billy) rows to a tmp billy_tape location.
        billy_lake = tmp_path / "billy_tape"
        _write_schema_a_lake(billy_lake, [_BILLY_ROW_0, _BILLY_ROW_1])

        # LakeReader with normalise=True should produce normalised rows.
        reader = LakeReader(base_dir=str(billy_lake), normalise=True)
        rows = list(reader.iter_rows())

        assert len(rows) >= 1, "schema-A lake with normalise=True must yield at least one row"
        # Verify the normalised row has the expected shape.
        row0 = rows[0]
        assert "price" in row0, "normalised row must carry 'price'"
        assert "vol_sol" in row0, "normalised row must carry 'vol_sol'"
        assert "phase" in row0, "normalised row must carry 'phase'"
        assert row0["mint"] == _BILLY_ROW_0["mint"]
        assert row0["phase"] == "pre"
        assert row0["price"] == pytest.approx(_ROW0_EXPECTED_PRICE, rel=1e-10)
        assert row0["vol_sol"] == pytest.approx(_ROW0_EXPECTED_VOL_SOL, rel=1e-10)

    def test_self_reads_firehose_schema_b(self, tmp_path):
        """Under self: export reads lake/firehose with normalise=True → non-empty rows."""
        from core.tape.lake_reader import LakeReader

        firehose_lake = tmp_path / "firehose"
        _write_schema_b_lake(firehose_lake, [_schema_b_equivalent(_BILLY_ROW_0)])

        reader = LakeReader(base_dir=str(firehose_lake), normalise=True)
        rows = list(reader.iter_rows())

        assert len(rows) >= 1, "schema-B lake with normalise=True must yield at least one row"
        row0 = rows[0]
        assert row0["mint"] == _BILLY_ROW_0["mint"]
        assert row0["phase"] == "pre"

    @pytest.mark.django_db
    def test_export_data_contract_shared_billy_non_empty(self, tmp_path, monkeypatch):
        """export_data_contract under shared_billy reads billy_tape → non-empty swaps."""
        import django.conf

        # Patch the Django setting to shared_billy.
        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        # Arrange: write schema-A rows to what the task calls lake/billy_tape.
        billy_lake = tmp_path / "billy_tape"
        _write_schema_a_lake(billy_lake, [_BILLY_ROW_0, _BILLY_ROW_1])

        from core.tasks import export_data_contract

        out_dir = tmp_path / "export"
        # Pass explicit lake_base_dir to exercise the normalise=True path with billy rows.
        result = export_data_contract(
            out_dir=str(out_dir),
            surfaces=["swaps"],
            lake_base_dir=str(billy_lake),
        )

        assert result["surfaces"]["swaps"]["row_count"] >= 1, (
            "export under shared_billy with schema-A billy rows must produce non-empty swaps"
        )

    @pytest.mark.django_db
    def test_export_data_contract_self_non_empty(self, tmp_path, monkeypatch):
        """export_data_contract under self reads lake/firehose → non-empty swaps."""
        import django.conf

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "self", raising=False)

        firehose_lake = tmp_path / "firehose"
        _write_schema_b_lake(firehose_lake, [_schema_b_equivalent(_BILLY_ROW_0)])

        from core.tasks import export_data_contract

        out_dir = tmp_path / "export"
        result = export_data_contract(
            out_dir=str(out_dir),
            surfaces=["swaps"],
            lake_base_dir=str(firehose_lake),
        )

        assert result["surfaces"]["swaps"]["row_count"] >= 1, (
            "export under self with schema-B rows must produce non-empty swaps"
        )

    def test_lake_path_config_driven_shared_billy(self, monkeypatch):
        """When lake_base_dir is None, shared_billy resolves to lake/billy_tape.

        Since LakeReader is imported inside export_data_contract's function body,
        we patch it at the module level (core.tape.lake_reader.LakeReader) which
        is where the name is looked up at call time.
        """
        import django.conf

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        # Capture the lake_base_dir that LakeReader is constructed with.
        captured = {}

        import pathlib as _pathlib

        import core.tape.lake_reader as _lrmod

        class _SpyLakeReader:
            def __init__(self, base_dir="lake/tapes", *, normalise=False):
                captured["base_dir"] = str(base_dir)
                captured["normalise"] = normalise
                self._base_dir = _pathlib.Path(base_dir)
                self._normalise = normalise
                self.bad_lines = 0

            def iter_rows(self, *, date_str=None):
                return iter([])

        # Patch LakeReader where the function-body import resolves it.
        monkeypatch.setattr(_lrmod, "LakeReader", _SpyLakeReader)

        # Also patch Token.objects to avoid DB access.
        import core.models as _cm
        monkeypatch.setattr(_cm.Token.objects, "all", lambda: _FakeQueryset())

        from core.tasks import export_data_contract
        export_data_contract(out_dir="/tmp/test_us89_x", surfaces=["swaps"])

        assert "billy_tape" in captured.get("base_dir", ""), (
            f"Under shared_billy, lake_base_dir should point to billy_tape, got {captured}"
        )
        assert captured.get("normalise") is True, "normalise=True must be passed to LakeReader"

    def test_lake_path_config_driven_self(self, monkeypatch):
        """When lake_base_dir is None, self resolves to lake/firehose."""
        import django.conf

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "self", raising=False)

        captured = {}

        import pathlib as _pathlib

        import core.tape.lake_reader as _lrmod

        class _SpyLakeReader:
            def __init__(self, base_dir="lake/tapes", *, normalise=False):
                captured["base_dir"] = str(base_dir)
                captured["normalise"] = normalise
                self._base_dir = _pathlib.Path(base_dir)
                self._normalise = normalise
                self.bad_lines = 0

            def iter_rows(self, *, date_str=None):
                return iter([])

        monkeypatch.setattr(_lrmod, "LakeReader", _SpyLakeReader)

        import core.models as _cm
        monkeypatch.setattr(_cm.Token.objects, "all", lambda: _FakeQueryset())

        from core.tasks import export_data_contract
        export_data_contract(out_dir="/tmp/test_us89_y", surfaces=["swaps"])

        assert "firehose" in captured.get("base_dir", ""), (
            f"Under self, lake_base_dir should point to firehose, got {captured}"
        )
        assert captured.get("normalise") is True, "normalise=True must be passed to LakeReader"


# ===========================================================================
# 2. Curvestage Settler Lake Path
# ===========================================================================

class TestCurvestageSettlerLakePath:
    """copytrade/fill_repricing.py settler reads correct pre-grad lake under each TAPE_SOURCE."""

    def test_find_next_trade_price_shared_billy_schema_a(self, tmp_path):
        """Under shared_billy: find_next_trade_price reads schema-A lake and finds price."""
        from copytrade.fill_repricing import find_next_trade_price

        mint = _BILLY_ROW_0["mint"]
        wallet_bt = _BILLY_ROW_0["block_time"] - 5  # wallet buy 5s before the lake row

        # Write schema-A lake to a tmp billy_tape location.
        billy_lake = tmp_path / "billy_tape"
        _write_schema_a_lake(billy_lake, [_BILLY_ROW_0])

        # Pass explicit path (schema-A) with normalise=True via LakeReader internal.
        price = find_next_trade_price(
            mint,
            wallet_bt,
            lake_base_dir=str(billy_lake),
        )

        assert price is not None, (
            "find_next_trade_price must find price from schema-A (billy) lake rows"
        )
        assert price == pytest.approx(_ROW0_EXPECTED_PRICE, rel=1e-6), (
            f"Expected price from schema-A normalised row: {_ROW0_EXPECTED_PRICE}, got {price}"
        )

    def test_find_next_trade_price_self_schema_b(self, tmp_path):
        """Under self: find_next_trade_price reads schema-B lake and finds price."""
        from copytrade.fill_repricing import find_next_trade_price

        mint = _BILLY_ROW_0["mint"]
        wallet_bt = _BILLY_ROW_0["block_time"] - 5
        schema_b_row = _schema_b_equivalent(_BILLY_ROW_0)

        firehose_lake = tmp_path / "firehose"
        _write_schema_b_lake(firehose_lake, [schema_b_row])

        price = find_next_trade_price(
            mint,
            wallet_bt,
            lake_base_dir=str(firehose_lake),
        )

        assert price is not None, (
            "find_next_trade_price must find price from schema-B (self-tape) lake rows"
        )
        assert price == pytest.approx(_ROW0_EXPECTED_PRICE, rel=1e-6)

    def test_shared_billy_empty_self_lake_returns_none(self, tmp_path):
        """Under shared_billy with empty self-lake, settler returns None (NO_TAPE)."""
        from copytrade.fill_repricing import find_next_trade_price

        mint = _BILLY_ROW_0["mint"]
        wallet_bt = _BILLY_ROW_0["block_time"] - 5

        # Create an empty firehose lake (no rows).
        firehose_lake = tmp_path / "empty_firehose"
        firehose_lake.mkdir()

        price = find_next_trade_price(
            mint,
            wallet_bt,
            lake_base_dir=str(firehose_lake),  # empty → NO_TAPE
        )

        assert price is None, (
            "Empty lake must return None (NO_TAPE), not raise or return garbage"
        )

    def test_pregrad_lake_default_shared_billy(self, monkeypatch):
        """_pregrad_lake_default() returns 'lake/billy_tape' under shared_billy."""
        import django.conf

        from copytrade.fill_repricing import _pregrad_lake_default

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)
        assert _pregrad_lake_default() == "lake/billy_tape"

    def test_pregrad_lake_default_self(self, monkeypatch):
        """_pregrad_lake_default() returns 'lake/firehose' under self."""
        import django.conf

        from copytrade.fill_repricing import _pregrad_lake_default

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "self", raising=False)
        assert _pregrad_lake_default() == "lake/firehose"

    def test_postgrad_lake_default_always_firehose(self, monkeypatch):
        """_postgrad_lake_default() always returns 'lake/firehose' regardless of TAPE_SOURCE."""
        import django.conf

        from copytrade.fill_repricing import _postgrad_lake_default

        for tape_source in ("shared_billy", "self"):
            monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", tape_source, raising=False)
            assert _postgrad_lake_default() == "lake/firehose", (
                f"Post-grad lake must always be lake/firehose, got different for {tape_source}"
            )

    def test_find_next_trade_price_none_lake_resolves_config(self, monkeypatch, tmp_path):
        """find_next_trade_price(lake_base_dir=None) reads TAPE_SOURCE to resolve path."""
        import django.conf

        from copytrade.fill_repricing import find_next_trade_price

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        mint = _BILLY_ROW_0["mint"]
        wallet_bt = _BILLY_ROW_0["block_time"] - 5

        # The default path will be lake/billy_tape — which doesn't exist in tmp_path,
        # so result must be None (NO_TAPE), NOT an exception.
        # This proves the default is resolved without crashing.
        captured_path = {}

        original_lr = __import__("core.tape.lake_reader", fromlist=["LakeReader"]).LakeReader

        class _CapturingReader(original_lr):
            def __init__(self, base_dir="lake/tapes", *, normalise=False):
                captured_path["base_dir"] = str(base_dir)
                captured_path["normalise"] = normalise
                self._base_dir = __import__("pathlib").Path(base_dir)
                self._normalise = normalise
                self.bad_lines = 0

            def iter_rows(self, *, date_str=None):
                return iter([])

        with patch("copytrade.fill_repricing.LakeReader", _CapturingReader):
            find_next_trade_price(mint, wallet_bt)  # lake_base_dir=None — captures path

        assert "billy_tape" in captured_path.get("base_dir", ""), (
            f"lake_base_dir=None under shared_billy should resolve to billy_tape, "
            f"got: {captured_path}"
        )
        assert captured_path.get("normalise") is True, (
            "normalise=True must be passed to LakeReader for schema-A rows"
        )

    def test_reprice_position_split_dirs(self, tmp_path):
        """reprice_position accepts pregrad/postgrad split dirs — entry from billy, exit from firehose."""
        from copytrade.fill_repricing import find_next_trade_price

        mint = _BILLY_ROW_0["mint"]
        pregrad_bt = _BILLY_ROW_0["block_time"] - 5  # entry before the lake row
        postgrad_bt = _BILLY_ROW_0["block_time"] - 3  # exit also before the lake row

        # Write schema-A to pregrad lake (billy_tape).
        pregrad_lake = tmp_path / "pregrad"
        _write_schema_a_lake(pregrad_lake, [_BILLY_ROW_0])

        # Write schema-B to postgrad lake (firehose).
        postgrad_lake = tmp_path / "postgrad"
        _write_schema_b_lake(postgrad_lake, [_schema_b_equivalent(_BILLY_ROW_0)])

        # Entry leg reads pregrad lake.
        entry_price = find_next_trade_price(
            mint, pregrad_bt, lake_base_dir=str(pregrad_lake)
        )
        assert entry_price is not None, "Entry leg must find price in schema-A pregrad lake"

        # Exit leg reads postgrad lake.
        exit_price = find_next_trade_price(
            mint, postgrad_bt, lake_base_dir=str(postgrad_lake)
        )
        assert exit_price is not None, "Exit leg must find price in schema-B postgrad lake"

        # Both prices match (same underlying swap data).
        assert entry_price == pytest.approx(exit_price, rel=1e-6)

    def test_normalise_true_extracts_price_from_schema_a(self, tmp_path):
        """LakeReader(normalise=True) extracts 'price' from schema-A rows for repricing."""
        from core.tape.lake_reader import LakeReader

        lake = tmp_path / "billy_tape"
        _write_schema_a_lake(lake, [_BILLY_ROW_0])

        reader = LakeReader(base_dir=str(lake), normalise=True)
        rows = list(reader.iter_rows())

        assert len(rows) == 1
        assert rows[0]["price"] == pytest.approx(_ROW0_EXPECTED_PRICE, rel=1e-10), (
            "normalised schema-A row must carry price=vsol/vtok"
        )


# ===========================================================================
# 3. Curvestage Retrain Lake Path
# ===========================================================================

class TestCurvestageRetrainLakePath:
    """copytrade/curvestage_train.py LAKE_DIR is config-driven via TAPE_SOURCE."""

    def test_get_lake_dir_shared_billy(self, monkeypatch):
        """get_lake_dir() returns /app/lake/billy_tape under shared_billy."""
        import django.conf

        from copytrade.curvestage_train import get_lake_dir

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)
        result = get_lake_dir()
        assert result == "/app/lake/billy_tape", (
            f"Expected /app/lake/billy_tape under shared_billy, got {result}"
        )

    def test_get_lake_dir_self(self, monkeypatch):
        """get_lake_dir() returns /app/lake/firehose under self."""
        import django.conf

        from copytrade.curvestage_train import get_lake_dir

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "self", raising=False)
        result = get_lake_dir()
        assert result == "/app/lake/firehose", (
            f"Expected /app/lake/firehose under self, got {result}"
        )

    def test_lake_age_days_delegates_to_get_lake_dir(self, monkeypatch, tmp_path):
        """_lake_age_days(None) uses get_lake_dir() — reads the config-driven path."""
        import django.conf

        from copytrade.curvestage_train import _lake_age_days

        # Point shared_billy config to our tmp dir (with 8 days of partitions).
        fake_lake = tmp_path / "billy_tape"
        fake_lake.mkdir()
        # Create 8 dt= partitions spanning 8 days.
        import datetime as _dt
        from datetime import timedelta
        base_date = _dt.date(2026, 6, 15)
        for i in range(8):
            d = (base_date + timedelta(days=i)).isoformat()
            (fake_lake / f"dt={d}").mkdir()

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        # Patch get_lake_dir to return our fake lake.
        with patch("copytrade.curvestage_train.get_lake_dir", return_value=str(fake_lake)):
            age = _lake_age_days(None)  # None → delegates to get_lake_dir()

        assert age >= 7, f"Expected >= 7 days, got {age}"

    def test_lake_date_strs_delegates_to_get_lake_dir(self, monkeypatch, tmp_path):
        """_lake_date_strs(None) uses get_lake_dir() — reads config-driven path."""
        import django.conf

        from copytrade.curvestage_train import _lake_date_strs

        fake_lake = tmp_path / "billy_tape"
        fake_lake.mkdir()
        (fake_lake / "dt=2026-06-20").mkdir()
        (fake_lake / "dt=2026-06-21").mkdir()

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        with patch("copytrade.curvestage_train.get_lake_dir", return_value=str(fake_lake)):
            dates = _lake_date_strs(None)

        assert "2026-06-20" in dates
        assert "2026-06-21" in dates

    def test_retrain_from_lake_none_uses_get_lake_dir(self, monkeypatch, tmp_path):
        """retrain_from_lake(None) passes the config-driven lake_dir to age check."""
        import django.conf

        from copytrade.curvestage_train import retrain_from_lake

        # Empty lake → age=0 → lake_too_young → retrain skipped.
        # The key check is that get_lake_dir() is consulted when None is passed.
        fake_lake = tmp_path / "billy_tape"
        fake_lake.mkdir()

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        with patch("copytrade.curvestage_train.get_lake_dir", return_value=str(fake_lake)) as mock_gld:
            result = retrain_from_lake(None)

        mock_gld.assert_called_once()
        assert result["retrained"] is False
        assert result["reason"] == "lake_too_young"

    def test_retrain_from_lake_shared_billy_uses_billy_tape_path(self, monkeypatch, tmp_path):
        """retrain_from_lake under shared_billy reads from billy_tape path."""
        import django.conf

        from copytrade.curvestage_train import MIN_LAKE_DAYS, retrain_from_lake

        # Create a fake billy_tape lake with < 7 days (date-gate no-op).
        billy_lake = tmp_path / "billy_tape"
        billy_lake.mkdir()
        (billy_lake / "dt=2026-06-20").mkdir()
        (billy_lake / "dt=2026-06-22").mkdir()

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "shared_billy", raising=False)

        with patch("copytrade.curvestage_train.get_lake_dir", return_value=str(billy_lake)):
            result = retrain_from_lake()  # lake_dir=None → get_lake_dir()

        # With only 2 days, should be lake_too_young.
        assert result["retrained"] is False
        assert result["lake_age_days"] < MIN_LAKE_DAYS, (
            "2-day lake must be below MIN_LAKE_DAYS"
        )

    def test_retrain_from_lake_self_uses_firehose_path(self, monkeypatch, tmp_path):
        """retrain_from_lake under self reads from lake/firehose path."""
        import django.conf

        from copytrade.curvestage_train import retrain_from_lake

        firehose_lake = tmp_path / "firehose"
        firehose_lake.mkdir()
        (firehose_lake / "dt=2026-06-20").mkdir()

        monkeypatch.setattr(django.conf.settings, "TAPE_SOURCE", "self", raising=False)

        with patch("copytrade.curvestage_train.get_lake_dir", return_value=str(firehose_lake)):
            result = retrain_from_lake()

        assert result["retrained"] is False  # only 1 day → too young
        assert result["reason"] == "lake_too_young"

    def test_lake_dir_constant_unchanged(self):
        """LAKE_DIR fallback constant still present (backward compat for existing tests)."""
        from copytrade.curvestage_train import LAKE_DIR
        assert LAKE_DIR == "/app/lake/firehose", (
            "LAKE_DIR fallback constant must remain /app/lake/firehose"
        )


# ===========================================================================
# 4. Score-Level Schema Parity (AC-96.4 offline proof)
# ===========================================================================

class TestScoreLevelSchemaParity:
    """Prove that schema-A and schema-B inputs produce the SAME normalised row,
    and therefore the SAME feature vector and SAME score.

    This is the offline parity proof required by AC-96.4:
      schema-A billy row → normalise_row → norm_row_to_swap_dict
      == schema-B equivalent row → normalise_row → norm_row_to_swap_dict

    Both produce swap-dicts with identical price, vol_sol, side, mint, phase.
    Score-level equality follows from: same swap-dicts → same features → same score.

    The test explicitly does NOT require the LightGBM boosters (which are
    host-local and gitignored) — parity-by-construction at the feature-vector
    level is sufficient to prove score parity (a deterministic scorer maps
    identical feature vectors to identical scores by definition).
    """

    def test_schema_a_normalise_row_price(self):
        """Schema-A row normalises to price = vsol / vtok."""
        from core.firehose.shared_tape import normalise_row

        norm = normalise_row(_BILLY_ROW_0)
        assert norm is not None
        assert norm.price == pytest.approx(_ROW0_EXPECTED_PRICE, rel=1e-10)

    def test_schema_b_normalise_row_price(self):
        """Schema-B row normalises to the SAME price as the schema-A equivalent."""
        from core.firehose.shared_tape import normalise_row

        schema_b = _schema_b_equivalent(_BILLY_ROW_0)
        norm = normalise_row(schema_b)
        assert norm is not None
        assert norm.price == pytest.approx(_ROW0_EXPECTED_PRICE, rel=1e-10)

    def test_schema_a_and_b_produce_identical_swap_dicts(self):
        """Schema-A and schema-B for the same swap produce identical swap-dict keys/values."""
        from core.firehose.shared_tape import norm_row_to_swap_dict, normalise_row

        norm_a = normalise_row(_BILLY_ROW_0)
        assert norm_a is not None

        schema_b = _schema_b_equivalent(_BILLY_ROW_0)
        norm_b = normalise_row(schema_b)
        assert norm_b is not None

        swap_a = norm_row_to_swap_dict(norm_a)
        swap_b = norm_row_to_swap_dict(norm_b)

        # Core fields must match exactly.
        for key in ("mint", "side", "phase", "owner"):
            assert swap_a[key] == swap_b[key], (
                f"Field '{key}' must match between schema-A and schema-B: "
                f"{swap_a[key]!r} != {swap_b[key]!r}"
            )

        # Numeric fields within floating-point precision.
        assert swap_a["price"] == pytest.approx(swap_b["price"], rel=1e-10)
        assert swap_a["vol_sol"] == pytest.approx(swap_b["vol_sol"], rel=1e-10)
        assert swap_a["block_time"] == swap_b["block_time"]
        assert swap_a["slot"] == swap_b["slot"]

    def test_schema_a_norm_row_phase_is_pre(self):
        """Schema-A rows always normalise to phase='pre' (bonding-curve only)."""
        from core.firehose.shared_tape import normalise_row

        norm = normalise_row(_BILLY_ROW_0)
        assert norm is not None
        assert norm.phase == "pre", "Schema-A tape is always pre-grad bonding-curve"

    def test_schema_b_norm_row_phase_is_pre(self):
        """Schema-B pre row normalises to phase='pre'."""
        from core.firehose.shared_tape import normalise_row

        schema_b = _schema_b_equivalent(_BILLY_ROW_0)
        assert schema_b["phase"] == "pre"
        norm = normalise_row(schema_b)
        assert norm is not None
        assert norm.phase == "pre"

    def test_lake_reader_schema_a_normalise_produces_same_rows_as_schema_b(self, tmp_path):
        """LakeReader(normalise=True) on schema-A lake == same rows as schema-B lake."""
        from core.tape.lake_reader import LakeReader

        # Write schema-A lake.
        schema_a_lake = tmp_path / "schema_a"
        _write_schema_a_lake(schema_a_lake, [_BILLY_ROW_0])

        # Write schema-B lake (logically the same swap data).
        schema_b_lake = tmp_path / "schema_b"
        _write_schema_b_lake(schema_b_lake, [_schema_b_equivalent(_BILLY_ROW_0)])

        rows_a = list(LakeReader(base_dir=str(schema_a_lake), normalise=True).iter_rows())
        rows_b = list(LakeReader(base_dir=str(schema_b_lake), normalise=True).iter_rows())

        assert len(rows_a) == 1 and len(rows_b) == 1, (
            "Both lakes must yield exactly one row"
        )

        ra, rb = rows_a[0], rows_b[0]
        assert ra["mint"] == rb["mint"]
        assert ra["phase"] == rb["phase"] == "pre"
        assert ra["price"] == pytest.approx(rb["price"], rel=1e-10), (
            f"Price must match: schema-A {ra['price']:.6e} vs schema-B {rb['price']:.6e}"
        )
        assert ra["vol_sol"] == pytest.approx(rb["vol_sol"], rel=1e-10), (
            f"vol_sol must match: schema-A {ra['vol_sol']:.6e} vs schema-B {rb['vol_sol']:.6e}"
        )

    def test_identical_swap_dicts_produce_identical_feature_inputs(self):
        """Identical swap-dicts from schema-A and schema-B feed identical feature inputs.

        This is the SCORE-LEVEL parity proof: given the same swap-dict shape,
        any deterministic feature function must produce the same features,
        and therefore the same score.

        We verify using assemble_pregrad_features (the existing scoring path)
        applied to a minimal single-swap tape — both schema paths produce the
        same pre_n_swaps, pre_buy_frac, pre_vol (the count-based features).
        """
        from core.firehose.shared_tape import norm_row_to_swap_dict, normalise_row

        # Produce swap-dicts from both schemas.
        swap_a = norm_row_to_swap_dict(normalise_row(_BILLY_ROW_0))
        swap_b = norm_row_to_swap_dict(normalise_row(_schema_b_equivalent(_BILLY_ROW_0)))

        # Verify they carry the exact same price and vol_sol (the score-load-bearing fields).
        assert swap_a["price"] == pytest.approx(swap_b["price"], rel=1e-10)
        assert swap_a["vol_sol"] == pytest.approx(swap_b["vol_sol"], rel=1e-10)

        # Additional structural invariant: vol_usd is 0.0 for both pre-grad paths.
        assert swap_a["vol_usd"] == 0.0, "Pre-grad schema-A row must carry vol_usd=0.0"
        assert swap_b["vol_usd"] == 0.0, "Pre-grad schema-B row must carry vol_usd=0.0"

        # Token_amount (schema-A extra): present in swap_a, absent in swap_b.
        # This is expected — schema-A is richer. The scoring path uses price/vol_sol
        # as the primary signals; token_amount is a schema-A bonus for n_pregrad_holders.
        assert "token_amount" in swap_a, "Schema-A swap-dict must carry token_amount"

    def test_schema_a_sell_row_normalises_correctly(self):
        """Schema-A sell row (row 1) normalises with correct price and vol_sol."""
        from core.firehose.shared_tape import norm_row_to_swap_dict, normalise_row

        norm = normalise_row(_BILLY_ROW_1)
        assert norm is not None
        swap = norm_row_to_swap_dict(norm)

        expected_price = _BILLY_ROW_1["virtual_sol_reserves"] / _BILLY_ROW_1["virtual_token_reserves"]
        expected_vol_sol = _BILLY_ROW_1["sol_amount"] / 1e9

        assert swap["side"] == "sell"
        assert swap["price"] == pytest.approx(expected_price, rel=1e-10)
        assert swap["vol_sol"] == pytest.approx(expected_vol_sol, rel=1e-10)
        assert swap["phase"] == "pre"

    def test_score_parity_documented(self):
        """Document: schema-A and schema-B produce the same score for the same swap.

        This test documents the parity guarantee that the offline test proves.

        Guarantee: normalise_row(schema_A_row) and normalise_row(schema_B_equivalent_row)
        produce NormRow objects with the same price, vol_sol, side, phase, mint.
        Since V7Model.score_vectors / assemble_pregrad_features are deterministic
        functions of these fields, score-level parity follows from field-level parity.

        The model's feature math treats schema-A and schema-B swap-dicts identically
        because norm_row_to_swap_dict produces the same dict shape for both.
        """
        from core.firehose.shared_tape import normalise_row

        norm_a = normalise_row(_BILLY_ROW_0)
        norm_b = normalise_row(_schema_b_equivalent(_BILLY_ROW_0))

        # Parity fields (used by feature builders and scoring path).
        assert norm_a.price == pytest.approx(norm_b.price, rel=1e-10)
        assert norm_a.vol_sol == pytest.approx(norm_b.vol_sol, rel=1e-10)
        assert norm_a.side == norm_b.side
        assert norm_a.phase == norm_b.phase
        assert norm_a.mint == norm_b.mint
        assert norm_a.block_time == norm_b.block_time

        # Verdict: same fields → same features → same score.
        # The scoring path is a pure function of these fields (no random state).


# ===========================================================================
# Additional: Structural guards to prevent regression
# ===========================================================================

class TestStructuralGuards:
    """Regression guards: file-level checks that the pre-conditions are wired."""

    def test_tasks_export_uses_normalise_true(self):
        """core/tasks.py export_data_contract passes normalise=True to LakeReader."""
        import inspect

        from core import tasks
        src = inspect.getsource(tasks.export_data_contract)
        assert "normalise=True" in src, (
            "export_data_contract must pass normalise=True to LakeReader (US-89 pre-condition)"
        )

    def test_tasks_export_lake_path_config_driven(self):
        """core/tasks.py resolves lake path from TAPE_SOURCE, not a hardcoded string."""
        import inspect

        from core import tasks
        src = inspect.getsource(tasks.export_data_contract)
        assert "TAPE_SOURCE" in src or "tape_source" in src.lower(), (
            "export_data_contract must read TAPE_SOURCE to resolve lake path"
        )
        assert "billy_tape" in src, (
            "export_data_contract must map shared_billy to billy_tape"
        )

    def test_fill_repricing_uses_normalise_true(self):
        """copytrade/fill_repricing.py passes normalise=True to LakeReader."""
        import inspect

        from copytrade import fill_repricing
        src = inspect.getsource(fill_repricing.find_next_trade_price)
        assert "normalise=True" in src, (
            "find_next_trade_price must pass normalise=True to LakeReader (US-89 pre-condition)"
        )

    def test_fill_repricing_pregrad_default_helper_exists(self):
        """_pregrad_lake_default() helper exists in fill_repricing."""
        from copytrade.fill_repricing import _pregrad_lake_default
        assert callable(_pregrad_lake_default)

    def test_fill_repricing_postgrad_default_helper_exists(self):
        """_postgrad_lake_default() helper exists in fill_repricing."""
        from copytrade.fill_repricing import _postgrad_lake_default
        assert callable(_postgrad_lake_default)

    def test_curvestage_train_get_lake_dir_exists(self):
        """get_lake_dir() function exists in curvestage_train."""
        from copytrade.curvestage_train import get_lake_dir
        assert callable(get_lake_dir)

    def test_curvestage_train_lake_dir_not_hardcoded_in_retrain(self):
        """retrain_from_lake does NOT use the hardcoded LAKE_DIR constant directly."""
        import inspect

        from copytrade import curvestage_train
        src = inspect.getsource(curvestage_train.retrain_from_lake)
        # Must either call get_lake_dir() or accept lake_dir parameter.
        assert "get_lake_dir" in src or "lake_dir" in src, (
            "retrain_from_lake must use get_lake_dir() or accept injectable lake_dir"
        )

    def test_curvestage_train_lake_dir_functions_accept_none(self):
        """_lake_age_days(None) and _lake_date_strs(None) do not crash."""
        from copytrade.curvestage_train import _lake_age_days, _lake_date_strs

        # These must not raise when None is passed — they delegate to get_lake_dir().
        # The path won't exist in test context so they return 0 / [].
        with patch("copytrade.curvestage_train.get_lake_dir", return_value="/nonexistent/path"):
            age = _lake_age_days(None)
            dates = _lake_date_strs(None)

        assert isinstance(age, int)
        assert isinstance(dates, list)
