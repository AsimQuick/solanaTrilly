# ---
# module: core.tests.test_shared_firehose_us96
# sprint: sprint-15
# story: US-96
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-26
# dependencies: pytest, unittest.mock, gzip, json, os, time, pathlib,
#               core.firehose.shared_tape, core.tape.lake_reader
# ---
"""Tests for US-96 shared firehose: schema-A adapter, freshness check,
TAPE_SOURCE toggle, and parity between schema-A and schema-B paths.

VPS FINDINGS (2026-06-26 — read-only inspection, zero credits):
  Schema A confirmed present fields: virtual_sol_reserves, virtual_token_reserves,
    real_sol_reserves, sol_amount, token_amount, mint, slot, block_time,
    received_at, side, owner, signature.
  No vol_usd, no price, no phase (those are schema-B only).
  mtime cadence: updates approximately every 30s on the live part-file
    (mtime changed within a 30s poll window). Not just on ~45-min rotation.
  => max_age_s=600 (10 min) is SAFE: 20x the per-write cadence.

REAL FIXTURE ROWS (captured from /root/tape/dt=2026-06-26/part-1782452210-13.jsonl.gz):
  Row 0: buy 87ptU7np4H8goqe2WgEW7ups5m4PtsuCJgsXY9Kcpump
    vsol=31609313819, vtok=1018370730358943, sol_amount=98765431
    expected price = 31609313819 / 1018370730358943 ≈ 3.104e-5
    expected vol_sol = 98765431 / 1e9 ≈ 0.098765
    expected curve_frac = (31609313819/1e9 - 30) / 85 ≈ 0.01893
  Row 1: sell 3qiYGHUZiWoFKNA3ZbqMTFrCdfitNZHgofhghy9vpump
    vsol=32406456113, vtok=993320586867491
    expected price ≈ 3.262e-5
  Row 2: sell A1hnP1hq9FeHMiDajVb1E9W2QHwkjVP2B7hgGjWfpump
    vsol=47485102581, vtok=677896822282358
    expected price ≈ 7.003e-5
    expected depth_sol = 17485102581 / 1e9 ≈ 17.485
"""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---- Fixture constants derived from real VPS capture -------------------------
_ROW0_VSOL = 31609313819
_ROW0_VTOK = 1018370730358943
_ROW0_SOL_AMT = 98765431
_ROW0_EXPECTED_PRICE = _ROW0_VSOL / _ROW0_VTOK
_ROW0_EXPECTED_VOL_SOL = _ROW0_SOL_AMT / 1e9
_ROW0_EXPECTED_CURVE_FRAC = ((_ROW0_VSOL / 1e9) - 30.0) / 85.0

_ROW2_VSOL = 47485102581
_ROW2_REAL_SOL = 17485102581
_ROW2_VTOK = 677896822282358
_ROW2_EXPECTED_PRICE = _ROW2_VSOL / _ROW2_VTOK
_ROW2_EXPECTED_DEPTH_SOL = _ROW2_REAL_SOL / 1e9

# Real fixture rows (read-only capture from VPS — zero credits)
_SCHEMA_A_ROWS = [
    {
        "mint": "87ptU7np4H8goqe2WgEW7ups5m4PtsuCJgsXY9Kcpump",
        "slot": 428959724,
        "block_time": 1782452209,
        "received_at": 1782452210.133931,
        "side": "buy",
        "sol_amount": 98765431,
        "token_amount": 3191941405246,
        "owner": "9zAx7uPk1Rh3khufmFLsYh3RTRoxwvHEb51RwBBx3344",
        "signature": "5P1gWT4AndXJopS6132jvvZ5fekuH7m49SFv8Rdm8bSbjiVAEM947r3fZ8MsPtJdEir8JuLJCJRqAfbrwYvU5iA8",
        "virtual_sol_reserves": _ROW0_VSOL,
        "virtual_token_reserves": _ROW0_VTOK,
        "real_sol_reserves": 1609313819,
    },
    {
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
    },
    {
        "mint": "A1hnP1hq9FeHMiDajVb1E9W2QHwkjVP2B7hgGjWfpump",
        "slot": 428959725,
        "block_time": 1782452209,
        "received_at": 1782452210.530822,
        "side": "sell",
        "sol_amount": 184066355,
        "token_amount": 2617582822679,
        "owner": "72t9W6oSwYnE5yk2PTaVeReAm4Xy1YLs1YpmCt2FgrYq",
        "signature": "4wMGmdgA1TmuE1oX584U3fBmS9AdYA2rNfr3xbJ3c1NRKED2kvMRaaJohYMsXewGc4XFNZwhxzPSxiGKQ41GDKnx",
        "virtual_sol_reserves": _ROW2_VSOL,
        "virtual_token_reserves": _ROW2_VTOK,
        "real_sol_reserves": _ROW2_REAL_SOL,
    },
]

# Schema-B equivalent for the same mint as row 0 (for parity test)
_SCHEMA_B_ROW0_EQUIV = {
    "mint": "87ptU7np4H8goqe2WgEW7ups5m4PtsuCJgsXY9Kcpump",
    "block_time": 1782452209,
    "slot": 428959724,
    "signature": "5P1gWT4AndXJopS6132jvvZ5fekuH7m49SFv8Rdm8bSbjiVAEM947r3fZ8MsPtJdEir8JuLJCJRqAfbrwYvU5iA8",
    "price": _ROW0_EXPECTED_PRICE,
    "side": "buy",
    "vol": _ROW0_EXPECTED_VOL_SOL,
    "vol_sol": _ROW0_EXPECTED_VOL_SOL,
    "vol_usd": 0.0,
    "owner": "9zAx7uPk1Rh3khufmFLsYh3RTRoxwvHEb51RwBBx3344",
    "phase": "pre",
}


# ===========================================================================
# 1. Schema-A adapter tests (DoD-b requirement 1)
# ===========================================================================

class TestNormaliseRowSchemaA:
    """Verify schema-A normalisation against real VPS-captured fixture rows."""

    def setup_method(self):
        from core.firehose.shared_tape import norm_row_to_swap_dict, normalise_row
        self.normalise_row = normalise_row
        self.norm_row_to_swap_dict = norm_row_to_swap_dict

    def test_price_derived_from_reserves(self):
        """price = vsol / vtok (exact curve-state, from real row 0)."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert abs(row.price - _ROW0_EXPECTED_PRICE) < 1e-18

    def test_vol_sol_from_lamports(self):
        """vol_sol = sol_amount / 1e9."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert abs(row.vol_sol - _ROW0_EXPECTED_VOL_SOL) < 1e-12

    def test_curve_frac_from_reserves(self):
        """curve_frac_exact = (vsol/1e9 - 30) / 85 (exact from reserves)."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert row.curve_frac_exact is not None
        assert abs(row.curve_frac_exact - _ROW0_EXPECTED_CURVE_FRAC) < 1e-10

    def test_depth_sol_from_real_reserves(self):
        """depth_sol = real_sol_reserves / 1e9."""
        row = self.normalise_row(_SCHEMA_A_ROWS[2])
        assert row is not None
        assert row.depth_sol is not None
        assert abs(row.depth_sol - _ROW2_EXPECTED_DEPTH_SOL) < 1e-10

    def test_token_amount_preserved(self):
        """token_amount should be the raw integer from the row."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert row.token_amount == 3191941405246

    def test_phase_is_pre_for_schema_a(self):
        """schema-A rows are all pre-grad (no phase field)."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert row.phase == "pre"

    def test_vol_usd_zero_for_schema_a(self):
        """vol_usd=0 for schema-A (vol_usd=0 bug is pre-only; schema-A has no USD)."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert row.vol_usd == 0.0

    def test_side_preserved(self):
        """buy/sell is preserved correctly."""
        row_buy = self.normalise_row(_SCHEMA_A_ROWS[0])
        row_sell = self.normalise_row(_SCHEMA_A_ROWS[1])
        assert row_buy is not None and row_buy.side == "buy"
        assert row_sell is not None and row_sell.side == "sell"

    def test_mint_preserved(self):
        """mint is preserved from the raw row."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        assert row.mint == "87ptU7np4H8goqe2WgEW7ups5m4PtsuCJgsXY9Kcpump"

    def test_returns_none_for_missing_mint(self):
        """Row without mint (post-grad row) → None (not a crash)."""
        bad = dict(_SCHEMA_A_ROWS[0])
        del bad["mint"]
        assert self.normalise_row(bad) is None

    def test_returns_none_for_zero_vtok(self):
        """Row with virtual_token_reserves=0 → None (ZeroDivisionError guard)."""
        bad = dict(_SCHEMA_A_ROWS[0])
        bad["virtual_token_reserves"] = 0
        assert self.normalise_row(bad) is None

    def test_returns_none_for_bad_block_time(self):
        """Row with block_time=None → None."""
        bad = dict(_SCHEMA_A_ROWS[0])
        bad["block_time"] = None
        assert self.normalise_row(bad) is None

    def test_norm_row_to_swap_dict_contains_required_keys(self):
        """Converted dict must have the keys the scoring/settler loops expect."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        d = self.norm_row_to_swap_dict(row)
        for key in ("mint", "block_time", "slot", "signature", "price",
                    "side", "vol", "vol_sol", "vol_usd", "owner", "phase"):
            assert key in d, f"Missing key: {key}"

    def test_norm_row_to_swap_dict_schema_a_extras_present(self):
        """Schema-A extras (curve_frac_exact, depth_sol, token_amount) in swap dict."""
        row = self.normalise_row(_SCHEMA_A_ROWS[0])
        assert row is not None
        d = self.norm_row_to_swap_dict(row)
        assert "curve_frac_exact" in d
        assert "depth_sol" in d
        assert "token_amount" in d


class TestNormaliseRowSchemaB:
    """Verify schema-B normalisation (solanatrilly self-tape format)."""

    def setup_method(self):
        from core.firehose.shared_tape import normalise_row
        self.normalise_row = normalise_row

    def test_price_used_directly(self):
        """Schema-B uses the pre-computed price field directly."""
        row = self.normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row is not None
        assert abs(row.price - _ROW0_EXPECTED_PRICE) < 1e-18

    def test_vol_sol_preserved(self):
        """vol_sol is taken from the vol_sol field."""
        row = self.normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row is not None
        assert abs(row.vol_sol - _ROW0_EXPECTED_VOL_SOL) < 1e-12

    def test_phase_preserved(self):
        """phase is taken from the row."""
        row = self.normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row is not None
        assert row.phase == "pre"

    def test_curve_frac_none_for_schema_b(self):
        """Schema-B has no reserves → curve_frac_exact is None."""
        row = self.normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row is not None
        assert row.curve_frac_exact is None

    def test_returns_none_for_missing_price(self):
        """Schema-B without price → None."""
        bad = dict(_SCHEMA_B_ROW0_EQUIV)
        del bad["price"]
        assert self.normalise_row(bad) is None

    def test_returns_none_for_zero_price(self):
        """Schema-B with price=0 → None."""
        bad = dict(_SCHEMA_B_ROW0_EQUIV)
        bad["price"] = 0.0
        assert self.normalise_row(bad) is None


# ===========================================================================
# 2. A/B parity test (DoD-b requirement 2: same Row from both schemas)
# ===========================================================================

class TestSchemaABParity:
    """The same physical swap normalised from schema-A and schema-B produces
    equivalent price/vol_sol/side/mint (parity by construction)."""

    def test_price_parity(self):
        from core.firehose.shared_tape import normalise_row
        row_a = normalise_row(_SCHEMA_A_ROWS[0])
        row_b = normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row_a is not None and row_b is not None
        # Price must agree within floating-point rounding
        assert abs(row_a.price - row_b.price) < 1e-20

    def test_vol_sol_parity(self):
        from core.firehose.shared_tape import normalise_row
        row_a = normalise_row(_SCHEMA_A_ROWS[0])
        row_b = normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row_a is not None and row_b is not None
        assert abs(row_a.vol_sol - row_b.vol_sol) < 1e-12

    def test_side_parity(self):
        from core.firehose.shared_tape import normalise_row
        row_a = normalise_row(_SCHEMA_A_ROWS[0])
        row_b = normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row_a is not None and row_b is not None
        assert row_a.side == row_b.side

    def test_mint_parity(self):
        from core.firehose.shared_tape import normalise_row
        row_a = normalise_row(_SCHEMA_A_ROWS[0])
        row_b = normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row_a is not None and row_b is not None
        assert row_a.mint == row_b.mint

    def test_phase_parity(self):
        """Both schema-A and schema-B pre rows normalise to phase='pre'."""
        from core.firehose.shared_tape import normalise_row
        row_a = normalise_row(_SCHEMA_A_ROWS[0])
        row_b = normalise_row(_SCHEMA_B_ROW0_EQUIV)
        assert row_a is not None and row_b is not None
        assert row_a.phase == row_b.phase == "pre"


# ===========================================================================
# 3. Freshness precondition tests (DoD-d requirement)
# ===========================================================================

class TestBillyFirehoseIsLive:
    """billy_firehose_is_live() returns False on stale/missing, True on fresh."""

    def test_returns_false_when_no_parts(self, tmp_path):
        """No part files → False."""
        from core.firehose.shared_tape import billy_firehose_is_live
        result = billy_firehose_is_live(root=str(tmp_path), max_age_s=600)
        assert result is False

    def test_returns_false_when_all_stale(self, tmp_path):
        """Only old parts → False."""
        dt_dir = tmp_path / "dt=2020-01-01"
        dt_dir.mkdir()
        part = dt_dir / "part-0.jsonl.gz"
        part.write_bytes(b"")
        # backdate mtime to 1 hour ago
        old_time = time.time() - 3600
        os.utime(str(part), (old_time, old_time))
        from core.firehose.shared_tape import billy_firehose_is_live
        result = billy_firehose_is_live(root=str(tmp_path), max_age_s=600)
        assert result is False

    def test_returns_true_when_fresh(self, tmp_path):
        """Fresh part (just created) → True."""
        dt_dir = tmp_path / "dt=2026-06-26"
        dt_dir.mkdir()
        part = dt_dir / "part-0.jsonl.gz"
        part.write_bytes(b"")
        # mtime is just-now by default
        from core.firehose.shared_tape import billy_firehose_is_live
        result = billy_firehose_is_live(root=str(tmp_path), max_age_s=600)
        assert result is True

    def test_threshold_exactly_at_boundary(self, tmp_path):
        """Part exactly at max_age_s → False (>=, not >)."""
        dt_dir = tmp_path / "dt=2026-06-26"
        dt_dir.mkdir()
        part = dt_dir / "part-0.jsonl.gz"
        part.write_bytes(b"")
        boundary_time = time.time() - 600
        os.utime(str(part), (boundary_time, boundary_time))
        from core.firehose.shared_tape import billy_firehose_is_live
        result = billy_firehose_is_live(root=str(tmp_path), max_age_s=600)
        assert result is False

    def test_max_age_s_constant_value(self):
        """BILLY_TAPE_MAX_AGE_S must be 600 (VPS-verified cadence: 20x the ~30s flush)."""
        from core.firehose.shared_tape import BILLY_TAPE_MAX_AGE_S
        assert BILLY_TAPE_MAX_AGE_S == 600

    def test_stale_warning_exact_string(self):
        """The verbatim warning string is exactly the operator-mandated text."""
        from core.firehose.shared_tape import STALE_WARNING
        assert STALE_WARNING == "You must turn on solanaBilly's firehose."


# ===========================================================================
# 4. Inference enable-time freshness gate tests (DoD-d enforcement point i)
# ===========================================================================

class TestInferenceEnableFreshnessGate:
    """inference_control_view refuses to enable when TAPE_SOURCE=shared_billy
    and billy's tape is stale."""

    @pytest.mark.django_db
    def test_stale_tape_returns_409_and_warning(self, rf):
        """Stale tape → 409 with exact warning string; flag NOT flipped."""
        from core.models import PipelineState
        from core.pipeline_control_api import inference_control_view

        state = PipelineState.get()
        state.firehose_active = False
        state.scoring_enabled = False
        state.save()

        request = rf.post(
            "/api/control/inference/",
            data=json.dumps({"on": True}),
            content_type="application/json",
        )

        # The freshness check is called inside the function — patch the function
        # in the shared_tape module (that's where it lives, and pipeline_control_api
        # imports it inside the function body).
        with patch("core.firehose.shared_tape.billy_firehose_is_live", return_value=False), \
             patch("django.conf.settings.TAPE_SOURCE", "shared_billy", create=True):
            response = inference_control_view(request)

        assert response.status_code == 409
        from core.firehose.shared_tape import STALE_WARNING
        assert response.data["error"] == STALE_WARNING

        # Flag must NOT have been flipped
        state.refresh_from_db()
        assert state.firehose_active is False
        assert state.scoring_enabled is False

    @pytest.mark.django_db
    def test_fresh_tape_enables_inference(self, rf):
        """Fresh tape under shared_billy → 200, flag flipped."""
        from core.models import PipelineState
        from core.pipeline_control_api import inference_control_view

        state = PipelineState.get()
        state.firehose_active = False
        state.scoring_enabled = False
        state.save()

        request = rf.post(
            "/api/control/inference/",
            data=json.dumps({"on": True}),
            content_type="application/json",
        )

        with patch("core.firehose.shared_tape.billy_firehose_is_live", return_value=True), \
             patch("django.conf.settings.TAPE_SOURCE", "shared_billy", create=True):
            response = inference_control_view(request)

        assert response.status_code == 200
        assert response.data["firehose_active"] is True

    @pytest.mark.django_db
    def test_self_tape_source_skips_freshness_check(self, rf):
        """TAPE_SOURCE=self: no freshness check, enable succeeds regardless."""
        from core.models import PipelineState
        from core.pipeline_control_api import inference_control_view

        state = PipelineState.get()
        state.firehose_active = False
        state.scoring_enabled = False
        state.save()

        request = rf.post(
            "/api/control/inference/",
            data=json.dumps({"on": True}),
            content_type="application/json",
        )

        with patch("django.conf.settings.TAPE_SOURCE", "self", create=True):
            response = inference_control_view(request)

        assert response.status_code == 200
        assert response.data["firehose_active"] is True

    @pytest.mark.django_db
    def test_turn_off_skips_freshness_check(self, rf):
        """Turning off (on=False) never checks freshness."""
        from core.models import PipelineState
        from core.pipeline_control_api import inference_control_view

        state = PipelineState.get()
        state.firehose_active = True
        state.scoring_enabled = True
        state.save()

        request = rf.post(
            "/api/control/inference/",
            data=json.dumps({"on": False}),
            content_type="application/json",
        )

        # billy_firehose_is_live should NEVER be called on turn-off
        with patch("core.firehose.shared_tape.billy_firehose_is_live") as mock_fresh:
            response = inference_control_view(request)

        mock_fresh.assert_not_called()
        assert response.status_code == 200
        assert response.data["firehose_active"] is False


# ===========================================================================
# 5. TAPE_SOURCE toggle tests (DoD-b: TAPE_SOURCE=shared_billy vs self)
# ===========================================================================

class TestTapeSourceToggle:
    """Under TAPE_SOURCE=shared_billy, the daemon creates no tape_sink on_add.
    Under TAPE_SOURCE=self, the daemon creates the standard on_add hook."""

    def _make_daemon(self, tape_source: str):
        """Build a minimal FirehoseDaemon with a mocked tape_sink for the given tape_source."""
        # Inline import to avoid side effects at collection time
        from unittest.mock import MagicMock, patch

        # Patch settings
        with patch("django.conf.settings.TAPE_SOURCE", tape_source, create=True):
            from core.management.commands.run_firehose import FirehoseDaemon
            mock_sink = MagicMock()
            daemon = FirehoseDaemon(
                tape_sink=mock_sink,
                collection_factory=MagicMock(return_value=None),
                graduation_factory=MagicMock(return_value=(None, None)),
            )
            daemon._tape_source = tape_source  # ensure the attribute is set correctly
        return daemon, mock_sink

    def test_shared_billy_tape_has_no_on_add(self):
        """Under shared_billy (default), _tape has no on_add (no self-tape write).

        Note: Do NOT inject collection_factory/helius_factory — the seam-injection
        path forces TAPE_SOURCE=self so existing tests don't break.  For testing
        shared_billy behaviour, build with NO factory injection.
        """
        from core.management.commands.run_firehose import FirehoseDaemon
        mock_sink = MagicMock()
        # Build without legacy seam injection so TAPE_SOURCE is read from settings
        # (default "shared_billy" → _tape has no on_add hook).
        daemon = FirehoseDaemon(tape_sink=mock_sink)
        assert daemon._tape_source == "shared_billy", (
            f"Expected shared_billy, got {daemon._tape_source}"
        )
        # Adding a swap to _tape must NOT call the sink (no on_add)
        daemon._tape.add("mint1", {"mint": "mint1", "block_time": 1782452209})
        mock_sink.record.assert_not_called()

    def test_self_tape_has_on_add(self):
        """Under TAPE_SOURCE=self, _tape has the on_add hook (writes to sink)."""
        import django.conf
        orig = getattr(django.conf.settings, "TAPE_SOURCE", "shared_billy")
        try:
            django.conf.settings.TAPE_SOURCE = "self"
            from core.management.commands.run_firehose import FirehoseDaemon
            mock_sink = MagicMock()
            # Inject collection_factory so legacy seam activates TAPE_SOURCE=self
            daemon = FirehoseDaemon(
                tape_sink=mock_sink,
                collection_factory=MagicMock(return_value=None),
                graduation_factory=MagicMock(return_value=(None, None)),
            )
            daemon._tape.add("mint1", {"mint": "mint1", "block_time": 1782452209})
            mock_sink.record.assert_called_once()
        finally:
            django.conf.settings.TAPE_SOURCE = orig


# ===========================================================================
# 6. BillyTapeTailer poll tests (DoD-b: bad lines skip-and-count)
# ===========================================================================

class TestBillyTapeTailer:
    """BillyTapeTailer.poll_once_sync correctly reads new lines and counts bad rows."""

    def _write_part(self, tmp_path: Path, name: str, rows: list) -> Path:
        """Write a gzipped jsonl part file with the given rows."""
        dt_dir = tmp_path / "dt=2026-06-26"
        dt_dir.mkdir(exist_ok=True)
        path = dt_dir / name
        with gzip.open(str(path), "wb") as gz:
            for row in rows:
                gz.write((json.dumps(row) + "\n").encode("utf-8"))
        return path

    def test_reads_schema_a_rows(self, tmp_path):
        """Tailer reads and normalises schema-A rows from a part file."""
        from core.firehose.shared_tape import BillyTapeTailer
        self._write_part(tmp_path, "part-0.jsonl.gz", _SCHEMA_A_ROWS)
        tailer = BillyTapeTailer(root=str(tmp_path))
        rows = tailer.poll_once_sync()
        assert len(rows) == 3
        # First row is a buy — check price derivation
        buy_row = next(r for r in rows if r["side"] == "buy")
        assert abs(buy_row["price"] - _ROW0_EXPECTED_PRICE) < 1e-18

    def test_counts_bad_lines(self, tmp_path):
        """Malformed lines are skip-and-counted (not fatal)."""
        from core.firehose.shared_tape import BillyTapeTailer
        dt_dir = tmp_path / "dt=2026-06-26"
        dt_dir.mkdir(exist_ok=True)
        path = dt_dir / "part-0.jsonl.gz"
        with gzip.open(str(path), "wb") as gz:
            gz.write(b'not-json\n')
            gz.write((json.dumps(_SCHEMA_A_ROWS[0]) + "\n").encode("utf-8"))
            gz.write(b'also-bad\n')
        tailer = BillyTapeTailer(root=str(tmp_path))
        rows = tailer.poll_once_sync()
        assert len(rows) == 1   # only the valid row
        assert tailer.bad_lines == 2

    def test_post_grad_rows_skipped_and_counted(self, tmp_path):
        """Post-grad rows (no mint) are skipped and counted as bad_lines."""
        from core.firehose.shared_tape import BillyTapeTailer
        dt_dir = tmp_path / "dt=2026-06-26"
        dt_dir.mkdir(exist_ok=True)
        path = dt_dir / "part-0.jsonl.gz"
        post_row = {
            "block_time": 1782452209, "slot": 1, "side": "buy",
            "vol_usd": 12.5, "rel": 30, "signature": "abc", "owner": "def",
            "price": 0.001, "phase": "post",
            # NO mint
        }
        with gzip.open(str(path), "wb") as gz:
            gz.write((json.dumps(post_row) + "\n").encode("utf-8"))
            gz.write((json.dumps(_SCHEMA_A_ROWS[0]) + "\n").encode("utf-8"))
        tailer = BillyTapeTailer(root=str(tmp_path))
        rows = tailer.poll_once_sync()
        assert len(rows) == 1
        assert tailer.bad_lines == 1

    def test_only_reads_new_bytes(self, tmp_path):
        """Tailer tracks file offset and only reads newly appended bytes."""
        from core.firehose.shared_tape import BillyTapeTailer
        self._write_part(tmp_path, "part-0.jsonl.gz", [_SCHEMA_A_ROWS[0]])
        tailer = BillyTapeTailer(root=str(tmp_path))
        first = tailer.poll_once_sync()
        assert len(first) == 1
        # Second poll without new data → empty
        second = tailer.poll_once_sync()
        assert len(second) == 0

    def test_picks_up_new_part_files(self, tmp_path):
        """Tailer discovers new part files created after first poll."""
        from core.firehose.shared_tape import BillyTapeTailer
        # Write first part
        self._write_part(tmp_path, "part-0.jsonl.gz", [_SCHEMA_A_ROWS[0]])
        tailer = BillyTapeTailer(root=str(tmp_path))
        tailer.poll_once_sync()  # drain part-0
        # Write second part
        self._write_part(tmp_path, "part-1.jsonl.gz", [_SCHEMA_A_ROWS[1]])
        second = tailer.poll_once_sync()
        assert len(second) == 1
        assert second[0]["mint"] == _SCHEMA_A_ROWS[1]["mint"]


# ===========================================================================
# 7. LakeReader schema-A normalisation (DoD-b: single normalisation point)
# ===========================================================================

class TestLakeReaderSchemaA:
    """LakeReader normalises schema-A rows when reading from a billy_tape path."""

    def _write_part(self, base_dir: Path, rows: list) -> None:
        dt_dir = base_dir / "dt=2026-06-26"
        dt_dir.mkdir(parents=True, exist_ok=True)
        part = dt_dir / "part-0.jsonl.gz"
        with gzip.open(str(part), "wb") as gz:
            for row in rows:
                gz.write((json.dumps(row) + "\n").encode("utf-8"))

    def test_schema_a_normalised_by_lake_reader(self, tmp_path):
        """LakeReader yields normalised dicts from schema-A part files."""
        from core.tape.lake_reader import LakeReader
        self._write_part(tmp_path, _SCHEMA_A_ROWS)
        reader = LakeReader(base_dir=tmp_path, normalise=True)
        rows = list(reader.iter_rows())
        assert len(rows) == 3
        buy = next(r for r in rows if r["side"] == "buy")
        assert abs(buy["price"] - _ROW0_EXPECTED_PRICE) < 1e-18
        assert abs(buy["vol_sol"] - _ROW0_EXPECTED_VOL_SOL) < 1e-12
        assert buy["phase"] == "pre"

    def test_schema_b_still_works_with_normalise(self, tmp_path):
        """LakeReader also handles existing schema-B rows unchanged."""
        from core.tape.lake_reader import LakeReader
        self._write_part(tmp_path, [_SCHEMA_B_ROW0_EQUIV])
        reader = LakeReader(base_dir=tmp_path, normalise=True)
        rows = list(reader.iter_rows())
        assert len(rows) == 1
        assert abs(rows[0]["price"] - _ROW0_EXPECTED_PRICE) < 1e-18
        assert rows[0]["phase"] == "pre"

    def test_bad_lines_counted_not_fatal(self, tmp_path):
        """LakeReader counts normalisation failures as bad_lines (not fatal)."""
        from core.tape.lake_reader import LakeReader
        dt_dir = tmp_path / "dt=2026-06-26"
        dt_dir.mkdir()
        part = dt_dir / "part-0.jsonl.gz"
        # Post-grad row (no mint) should be counted as bad
        post_row = {"block_time": 1782452209, "side": "buy", "vol_usd": 5.0, "rel": 10}
        with gzip.open(str(part), "wb") as gz:
            gz.write((json.dumps(post_row) + "\n").encode("utf-8"))
            gz.write((json.dumps(_SCHEMA_A_ROWS[0]) + "\n").encode("utf-8"))
        reader = LakeReader(base_dir=tmp_path, normalise=True)
        rows = list(reader.iter_rows())
        assert len(rows) == 1
        assert reader.bad_lines == 1


# ===========================================================================
# 8. docker-compose.staging.yml guard tests (DoD-a requirement)
# ===========================================================================

class TestDockerComposeStaging:
    """Structural guards on docker-compose.staging.yml per US-96 §5a + §5e."""

    @pytest.fixture
    def compose(self):
        import yaml
        # Works both locally (3 levels up from core/tests/) and in Docker (/app/)
        candidates = [
            Path(__file__).resolve().parents[2] / "docker-compose.staging.yml",
            Path("/app/docker-compose.staging.yml"),
            Path(__file__).resolve().parents[3] / "docker-compose.staging.yml",
        ]
        for path in candidates:
            if path.exists():
                with open(path) as f:
                    return yaml.safe_load(f)
        pytest.skip("docker-compose.staging.yml not found")

    def test_inference_engine_has_billy_tape_ro_mount(self, compose):
        """inference_engine must have /root/tape:/app/lake/billy_tape:ro mount."""
        svc = compose["services"]["inference_engine"]
        volumes = svc.get("volumes", [])
        assert any(
            "/root/tape:/app/lake/billy_tape:ro" in v for v in volumes
        ), f"Missing :ro mount in inference_engine volumes: {volumes}"

    def test_celery_worker_has_billy_tape_ro_mount(self, compose):
        """celery-worker must have /root/tape:/app/lake/billy_tape:ro mount."""
        svc = compose["services"]["celery-worker"]
        volumes = svc.get("volumes", [])
        assert any(
            "/root/tape:/app/lake/billy_tape:ro" in v for v in volumes
        ), f"Missing :ro mount in celery-worker volumes: {volumes}"

    def test_inference_engine_helius_key_absent(self, compose):
        """HELIUS_API_KEY must NOT be in inference_engine environment (US-96 §5e)."""
        svc = compose["services"]["inference_engine"]
        env = svc.get("environment", {})
        if isinstance(env, dict):
            assert "HELIUS_API_KEY" not in env, \
                "HELIUS_API_KEY must be absent from inference_engine"
        else:
            # env is a list of "KEY=VAL" strings
            assert not any("HELIUS_API_KEY" in e for e in env), \
                "HELIUS_API_KEY must be absent from inference_engine"

    def test_listener_helius_key_present(self, compose):
        """HELIUS_API_KEY must remain on listener (for detection — NOT tape collection)."""
        svc = compose["services"]["listener"]
        env = svc.get("environment", {})
        if isinstance(env, dict):
            assert "HELIUS_API_KEY" in env
        else:
            assert any("HELIUS_API_KEY" in e for e in env)

    def test_celery_worker_helius_key_present(self, compose):
        """HELIUS_API_KEY must remain on celery-worker."""
        svc = compose["services"]["celery-worker"]
        env = svc.get("environment", {})
        if isinstance(env, dict):
            assert "HELIUS_API_KEY" in env
        else:
            assert any("HELIUS_API_KEY" in e for e in env)

    def test_copytrade_engine_helius_key_present(self, compose):
        """HELIUS_API_KEY must remain on copytrade_engine."""
        svc = compose["services"]["copytrade_engine"]
        env = svc.get("environment", {})
        if isinstance(env, dict):
            assert "HELIUS_API_KEY" in env
        else:
            assert any("HELIUS_API_KEY" in e for e in env)

    def test_inference_engine_has_tape_source(self, compose):
        """inference_engine must have TAPE_SOURCE env var (default shared_billy)."""
        svc = compose["services"]["inference_engine"]
        env = svc.get("environment", {})
        if isinstance(env, dict):
            assert "TAPE_SOURCE" in env
        else:
            assert any("TAPE_SOURCE" in e for e in env)


# ===========================================================================
# 9. Settings TAPE_SOURCE default value
# ===========================================================================

class TestSettingsTapeSource:
    """TAPE_SOURCE is defined in settings with the correct default."""

    def test_tape_source_default_is_shared_billy(self):
        """TAPE_SOURCE defaults to 'shared_billy' (not 'self')."""
        import django.conf
        assert hasattr(django.conf.settings, "TAPE_SOURCE")
        assert django.conf.settings.TAPE_SOURCE == "shared_billy"
