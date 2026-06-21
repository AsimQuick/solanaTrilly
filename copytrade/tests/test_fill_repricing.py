# ---
# module: copytrade.tests.test_fill_repricing
# sprint: epic/copy-paper-fill-repricing
# story: EPIC-copy-paper-fill-repricing
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: pytest, copytrade.fill_repricing, copytrade.models,
#   core.tape.lake_reader, copytrade.position_manager, copytrade.api
# ---
"""Test plan for EPIC-copy-paper-fill-repricing.

Covers:
  1. find_next_trade_price uses integer block offset (not sub-second float)
  2. find_next_trade_price excludes same-block rows
  3. find_next_trade_price crosses midnight correctly
  4. find_next_trade_price returns None on empty window
  5. reprice_position: within cap → REPRICED + PnL recomputed
  6. reprice_position: cap exceeded → ENTRY_REJECTED_TAPE
  7. reprice_position: no tape → NO_TAPE (keeps curve-sim price)
  8. Paper-week PnL direction: repriced net is break-even/negative vs inflated booked
  9. Summary API: total_trades excludes ENTRY_REJECTED; win_rate; n_rejected
 10. _update_pnl_by_wallet excludes ENTRY_REJECTED
 11. Trades view excludes ENTRY_REJECTED
 12. copy_latency_s formula pin (frozen — must not change)

Notes on Principle #7 (inject Clock):
  copytrade.fill_repricing uses NO datetime.now() / time.time() — all timestamps
  come from position.entry_ts / position.exit_ts (Django datetime objects, stored
  in UTC, converted to .timestamp() for the lake lookup).  Tests create positions
  with explicit datetimes.

Lake fixtures are written to tmp_path so the test is filesystem-clean and Docker-portable.
"""
from __future__ import annotations

import gzip
import json
import pathlib
from datetime import datetime, timezone
from typing import Optional

import pytest

from copytrade.fill_repricing import (
    REPRICE_BLOCK_OFFSET,
    REPRICE_STATUS_ENTRY_REJECTED_TAPE,
    REPRICE_STATUS_NO_TAPE,
    REPRICE_STATUS_REPRICED,
    REPRICE_WINDOW_S,
    find_next_trade_price,
    reprice_position,
)

# ---------------------------------------------------------------------------
# Lake fixture helpers
# ---------------------------------------------------------------------------

MINT_A = "Mint1111111111111111111111111111111111111111"
MINT_B = "Mint2222222222222222222222222222222222222222"


def _write_lake_rows(base_dir: pathlib.Path, date_str: str, rows: list[dict]) -> None:
    """Write rows to lake/firehose/dt=DATE/part-0.jsonl.gz."""
    part_dir = base_dir / f"dt={date_str}"
    part_dir.mkdir(parents=True, exist_ok=True)
    part_file = part_dir / "part-0.jsonl.gz"
    with gzip.open(str(part_file), "ab") as gz:
        for row in rows:
            gz.write((json.dumps(row) + "\n").encode("utf-8"))


def _row(
    mint: str,
    block_time: int,
    price: float,
    side: str = "buy",
    phase: str = "pre",
) -> dict:
    """Minimal lake row dict."""
    return {
        "mint": mint,
        "block_time": block_time,
        "slot": block_time * 2,  # arbitrary
        "signature": f"sig_{block_time}",
        "side": side,
        "price": price,
        "vol_sol": 0.1,
        "vol_usd": 15.0,
        "sol_usd": 150.0,
        "owner": "wallet1",
        "phase": phase,
    }


# ---------------------------------------------------------------------------
# 1. Integer block offset (WINDOW RULE load-bearing test)
# ---------------------------------------------------------------------------

class TestFindNextTradePrice:
    """Unit tests for find_next_trade_price."""

    def test_uses_integer_block_offset(self, tmp_path):
        """Row at wallet_bt+1 selected; same-block row excluded (block-based, not float)."""
        wallet_bt = 1_700_000_000  # arbitrary Unix epoch in 2023
        date_str = datetime.fromtimestamp(wallet_bt, tz=timezone.utc).strftime("%Y-%m-%d")

        # Row AT the wallet block (same block — must be excluded)
        same_block_row = _row(MINT_A, wallet_bt, price=1.0)
        # Row at wallet_bt + REPRICE_BLOCK_OFFSET (must be selected)
        next_block_row = _row(MINT_A, wallet_bt + REPRICE_BLOCK_OFFSET, price=2.0)
        # Row at wallet_bt + REPRICE_WINDOW_S + REPRICE_BLOCK_OFFSET + 1 (outside window)
        outside_row = _row(MINT_A, wallet_bt + REPRICE_WINDOW_S + REPRICE_BLOCK_OFFSET + 1, price=99.0)

        _write_lake_rows(tmp_path, date_str, [same_block_row, next_block_row, outside_row])

        result = find_next_trade_price(MINT_A, wallet_bt, lake_base_dir=str(tmp_path))
        assert result == pytest.approx(2.0), (
            f"Expected the next-block row price (2.0), got {result}"
        )

    # ---------------------------------------------------------------------------
    # 2. Same-block exclusion
    # ---------------------------------------------------------------------------

    def test_excludes_same_block(self, tmp_path):
        """Only same-block row in window → returns None (no next-block row available)."""
        wallet_bt = 1_700_000_000
        date_str = datetime.fromtimestamp(wallet_bt, tz=timezone.utc).strftime("%Y-%m-%d")

        # Only a same-block row — no row at or after wallet_bt+1
        _write_lake_rows(tmp_path, date_str, [_row(MINT_A, wallet_bt, price=5.0)])

        result = find_next_trade_price(MINT_A, wallet_bt, lake_base_dir=str(tmp_path))
        assert result is None, f"Expected None (only same-block row), got {result}"

    # ---------------------------------------------------------------------------
    # 3. Midnight crossing
    # ---------------------------------------------------------------------------

    def test_crosses_midnight(self, tmp_path):
        """Window that spans two UTC dates finds the row on the next date."""
        # Set wallet_bt close to midnight so the +10s window crosses into the next day
        # 2023-11-15 23:59:55 UTC = 1700092795
        wallet_bt = 1700092795
        low = wallet_bt + REPRICE_BLOCK_OFFSET
        high = wallet_bt + REPRICE_BLOCK_OFFSET + REPRICE_WINDOW_S

        low_dt = datetime.fromtimestamp(low, tz=timezone.utc)
        high_dt = datetime.fromtimestamp(high, tz=timezone.utc)
        date_str_today = low_dt.strftime("%Y-%m-%d")
        date_str_next = high_dt.strftime("%Y-%m-%d")

        # Verify this actually crosses midnight
        assert date_str_today != date_str_next, (
            f"Test setup: expected midnight crossing, but {date_str_today} == {date_str_next}; "
            "adjust wallet_bt"
        )

        # Write the row on the next-day partition at wallet_bt+5 (within window)
        row_bt = wallet_bt + 5
        _write_lake_rows(tmp_path, date_str_next, [_row(MINT_A, row_bt, price=3.0)])

        result = find_next_trade_price(MINT_A, wallet_bt, lake_base_dir=str(tmp_path))
        assert result == pytest.approx(3.0), (
            f"Expected row on next-day partition (price=3.0), got {result}"
        )

    # ---------------------------------------------------------------------------
    # 4. Empty window → None
    # ---------------------------------------------------------------------------

    def test_empty_window_returns_none(self, tmp_path):
        """No lake rows for mint at all → returns None (NO_TAPE)."""
        wallet_bt = 1_700_000_000
        result = find_next_trade_price(MINT_A, wallet_bt, lake_base_dir=str(tmp_path))
        assert result is None

    def test_different_mint_ignored(self, tmp_path):
        """Row for different mint in window → returns None."""
        wallet_bt = 1_700_000_000
        date_str = datetime.fromtimestamp(wallet_bt, tz=timezone.utc).strftime("%Y-%m-%d")
        _write_lake_rows(tmp_path, date_str, [_row(MINT_B, wallet_bt + 1, price=9.0)])

        result = find_next_trade_price(MINT_A, wallet_bt, lake_base_dir=str(tmp_path))
        assert result is None

    def test_first_row_selected_not_cheapest(self, tmp_path):
        """First row in block order (lowest block_time) is selected, not filtered by price."""
        wallet_bt = 1_700_000_000
        date_str = datetime.fromtimestamp(wallet_bt, tz=timezone.utc).strftime("%Y-%m-%d")

        # Two rows in window; the one with lower block_time should be selected
        row1 = _row(MINT_A, wallet_bt + 1, price=4.0)
        row2 = _row(MINT_A, wallet_bt + 2, price=100.0)
        _write_lake_rows(tmp_path, date_str, [row1, row2])

        result = find_next_trade_price(MINT_A, wallet_bt, lake_base_dir=str(tmp_path))
        assert result == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# 5-7. reprice_position tests using Django DB
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRepricePosition:
    """Integration tests for reprice_position using real Django ORM."""

    def _make_position(
        self,
        *,
        mint: str = MINT_A,
        entry_ts: datetime,
        exit_ts: Optional[datetime],
        entry_price: float,
        exit_price: float,
        sol_in: float = 0.1,
        quote_price: Optional[float] = None,
        exit_reason: str = "TIMER",
    ):
        """Create a minimal closed CopytradePosition row."""
        from copytrade.models import CopytradePosition
        sol_out = sol_in * (exit_price / entry_price)
        realized_pnl_sol = sol_out - sol_in
        realized_pnl_pct = (realized_pnl_sol / sol_in) * 100.0
        pos = CopytradePosition(
            cohort_id="test-cohort",
            mint=mint,
            trigger_wallet="wallet1" * 3,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=entry_ts,
            entry_price=entry_price,
            exit_ts=exit_ts,
            exit_price=exit_price,
            sol_in=sol_in,
            sol_out=sol_out,
            exit_reason=exit_reason,
            realized_pnl_sol=realized_pnl_sol,
            realized_pnl_pct=realized_pnl_pct,
            quote_price=quote_price,
        )
        pos.save()
        return pos

    # 5. Within cap → REPRICED
    def test_reprice_within_cap(self, tmp_path):
        """Entry repriced within 15% cap → status=REPRICED, PnL recomputed."""
        # Entry at curve-sim price 100.0 (inflated), quote 100.0
        # Lake has actual price 105.0 (within 15% cap)
        entry_ts = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc)
        entry_bt = int(entry_ts.timestamp())
        exit_bt = int(exit_ts.timestamp())
        date_str = entry_ts.strftime("%Y-%m-%d")

        # Write lake rows: entry leg (bt+1) and exit leg (bt+1)
        entry_lake_price = 105.0
        exit_lake_price = 120.0
        _write_lake_rows(tmp_path, date_str, [
            _row(MINT_A, entry_bt + 1, price=entry_lake_price),
        ])
        exit_date_str = exit_ts.strftime("%Y-%m-%d")
        _write_lake_rows(tmp_path, exit_date_str, [
            _row(MINT_A, exit_bt + 1, price=exit_lake_price),
        ])

        sol_in = 0.1
        # Inflated (fake-cheap) entry price
        inflated_entry = 100.0
        inflated_exit = 130.0
        pos = self._make_position(
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            entry_price=inflated_entry,
            exit_price=inflated_exit,
            sol_in=sol_in,
            quote_price=100.0,
        )
        status = reprice_position(pos.pk, lake_base_dir=str(tmp_path))
        assert status == REPRICE_STATUS_REPRICED

        from copytrade.models import CopytradePosition
        pos_refreshed = CopytradePosition.objects.get(pk=pos.pk)
        assert pos_refreshed.entry_reprice_status == REPRICE_STATUS_REPRICED
        assert pos_refreshed.entry_price == pytest.approx(entry_lake_price)
        assert pos_refreshed.exit_price == pytest.approx(exit_lake_price)
        # PnL should be recomputed: sol_in * (exit / entry) - sol_in
        expected_pnl = sol_in * (exit_lake_price / entry_lake_price) - sol_in
        assert pos_refreshed.realized_pnl_sol == pytest.approx(expected_pnl, rel=1e-6)

    # 6. Cap exceeded → ENTRY_REJECTED_TAPE
    def test_reprice_cap_exceeded(self, tmp_path):
        """Lake entry price > 15% above wallet quote → ENTRY_REJECTED_TAPE."""
        entry_ts = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc)
        entry_bt = int(entry_ts.timestamp())
        date_str = entry_ts.strftime("%Y-%m-%d")

        # Quote (wallet price) = 100.0; lake price = 120.0 → slip = 20% > 15% cap
        quote_price = 100.0
        lake_entry_price = 120.0
        _write_lake_rows(tmp_path, date_str, [
            _row(MINT_A, entry_bt + 1, price=lake_entry_price),
        ])

        pos = self._make_position(
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            entry_price=100.0,
            exit_price=130.0,
            quote_price=quote_price,
        )

        status = reprice_position(pos.pk, lake_base_dir=str(tmp_path))
        assert status == REPRICE_STATUS_ENTRY_REJECTED_TAPE

        from copytrade.models import CopytradePosition
        pos_refreshed = CopytradePosition.objects.get(pk=pos.pk)
        assert pos_refreshed.entry_reprice_status == REPRICE_STATUS_ENTRY_REJECTED_TAPE
        # PnL should not have been modified (still the original curve-sim PnL)
        assert pos_refreshed.entry_price == pytest.approx(100.0)

    # 7. No tape → NO_TAPE (keeps curve-sim price)
    def test_reprice_no_tape(self, tmp_path):
        """No lake row in window → NO_TAPE; curve-sim entry_price preserved."""
        entry_ts = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc)
        original_entry_price = 100.0
        original_exit_price = 130.0

        pos = self._make_position(
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            entry_price=original_entry_price,
            exit_price=original_exit_price,
        )
        # tmp_path is empty (no lake files)
        status = reprice_position(pos.pk, lake_base_dir=str(tmp_path))
        assert status == REPRICE_STATUS_NO_TAPE

        from copytrade.models import CopytradePosition
        pos_refreshed = CopytradePosition.objects.get(pk=pos.pk)
        assert pos_refreshed.entry_reprice_status == REPRICE_STATUS_NO_TAPE
        # Curve-sim price MUST be preserved (not blanked)
        assert pos_refreshed.entry_price == pytest.approx(original_entry_price)
        assert pos_refreshed.exit_price == pytest.approx(original_exit_price)


# ---------------------------------------------------------------------------
# 8. Paper-week PnL direction test
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPaperWeekPnlDirection:
    """Verify that repricing inflated curve-sim prices yields break-even/negative PnL.

    The epic reports ~+2.0 SOL booked vs ~+0.1 SOL repriced (break-even).
    This test locks the DIRECTION: a position that looks like a +50% winner
    at the fake-cheap entry price becomes break-even or negative when repriced
    at a more expensive (but still within cap) real-market price.
    """

    def test_repriced_pnl_is_worse_than_inflated(self, tmp_path):
        """Repriced PnL direction: inflated entry (fake-cheap) → repriced (real price) is smaller profit."""
        entry_ts = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc)
        entry_bt = int(entry_ts.timestamp())
        exit_bt = int(exit_ts.timestamp())
        date_str = entry_ts.strftime("%Y-%m-%d")

        sol_in = 0.1
        # Fake-cheap (curve-sim) entry: wallet quote = 100, our sim fill = 90
        # (curve-sim says we "got in cheaper" than the wallet)
        inflated_entry = 90.0    # curve-sim — too cheap (phantom fill)
        inflated_exit = 135.0    # 50% gain from fake entry
        wallet_quote = 100.0     # what the wallet actually paid

        # Real lake price at entry: wallet_quote * 1.05 = 105 (5% above wallet, within cap)
        # — a valid fill that's more expensive than the fake cheap entry
        real_entry_price = 105.0
        # Real lake price at exit: only slightly above real entry (break-even)
        real_exit_price = 108.0

        _write_lake_rows(tmp_path, date_str, [
            _row(MINT_A, entry_bt + 1, price=real_entry_price),
        ])
        exit_date_str = exit_ts.strftime("%Y-%m-%d")
        _write_lake_rows(tmp_path, exit_date_str, [
            _row(MINT_A, exit_bt + 1, price=real_exit_price),
        ])

        from copytrade.models import CopytradePosition
        sol_out_inflated = sol_in * (inflated_exit / inflated_entry)
        realized_pnl_inflated = sol_out_inflated - sol_in

        pos = CopytradePosition(
            cohort_id="paper-week-test",
            mint=MINT_A,
            trigger_wallet="wallet1" * 3,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=entry_ts,
            entry_price=inflated_entry,
            exit_ts=exit_ts,
            exit_price=inflated_exit,
            sol_in=sol_in,
            sol_out=sol_out_inflated,
            exit_reason="TIMER",
            realized_pnl_sol=realized_pnl_inflated,
            realized_pnl_pct=(realized_pnl_inflated / sol_in) * 100.0,
            quote_price=wallet_quote,
        )
        pos.save()

        inflated_pnl = float(pos.realized_pnl_sol)
        assert inflated_pnl > 0, f"Test setup: inflated PnL should be positive, got {inflated_pnl}"

        status = reprice_position(pos.pk, lake_base_dir=str(tmp_path))
        assert status == REPRICE_STATUS_REPRICED

        pos_refreshed = CopytradePosition.objects.get(pk=pos.pk)
        repriced_pnl = float(pos_refreshed.realized_pnl_sol)

        # DIRECTION LOCK: repriced PnL must be LESS than the inflated booked number
        assert repriced_pnl < inflated_pnl, (
            f"Repriced PnL ({repriced_pnl:.6f}) should be < inflated PnL ({inflated_pnl:.6f}). "
            "The repricing must reduce the phantom profit."
        )

        # The repriced result should be close to break-even (sol_in=0.1, entry=105, exit=108)
        expected_repriced_pnl = sol_in * (real_exit_price / real_entry_price) - sol_in
        assert repriced_pnl == pytest.approx(expected_repriced_pnl, rel=1e-6)


# ---------------------------------------------------------------------------
# 9. Summary API: win-rate denominator excludes ENTRY_REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSummaryWinRateExcludesEntryRejected:
    """Summary API must exclude ENTRY_REJECTED from trade count + win-rate."""

    def _make_closed_pos(
        self,
        *,
        cohort_id: str,
        mint: str,
        exit_reason: str,
        realized_pnl_sol: Optional[float],
        trigger_wallet: str = "wallet1" * 3,
    ):
        from copytrade.models import CopytradePosition
        pos = CopytradePosition(
            cohort_id=cohort_id,
            mint=mint,
            trigger_wallet=trigger_wallet,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc),
            exit_ts=datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc),
            exit_reason=exit_reason,
            entry_price=100.0,
            exit_price=110.0,
            sol_in=0.1,
            realized_pnl_sol=realized_pnl_sol,
            realized_pnl_pct=((realized_pnl_sol or 0.0) / 0.1 * 100) if realized_pnl_sol is not None else None,
        )
        pos.save()
        return pos

    def test_summary_win_rate_excludes_entry_rejected(self, client):
        """2 wins + 1 ENTRY_REJECTED → total=2, win_rate=1.0, n_rejected=1."""
        from copytrade.models import CopytradeCohort, CopyTradeSettings
        cohort_id = "test-summary-cohort"

        # Create cohort
        cohort = CopytradeCohort(
            cohort_id=cohort_id,
            created_at=datetime(2023, 11, 15, 0, 0, 0, tzinfo=timezone.utc),
            active=True,
        )
        cohort.save()

        # Set active cohort
        settings = CopyTradeSettings.get()
        settings.active_cohort_id = cohort_id
        settings.save()

        # 2 wins (TP)
        self._make_closed_pos(cohort_id=cohort_id, mint=MINT_A, exit_reason="TP", realized_pnl_sol=0.01)
        self._make_closed_pos(cohort_id=cohort_id, mint=MINT_B, exit_reason="TP", realized_pnl_sol=0.02)
        # 1 ENTRY_REJECTED (should be excluded)
        self._make_closed_pos(
            cohort_id=cohort_id,
            mint="Mint3333333333333333333333333333333333333333",
            exit_reason="ENTRY_REJECTED",
            realized_pnl_sol=None,
        )

        response = client.get("/api/copytrade/summary/")
        assert response.status_code == 200, response.content
        data = response.json()

        assert data["total_trades"] == 2, f"Expected 2 trades (excluding reject), got {data['total_trades']}"
        assert data["win_rate"] == pytest.approx(1.0), f"Expected win_rate=1.0, got {data['win_rate']}"
        assert data["n_rejected"] == 1, f"Expected n_rejected=1, got {data['n_rejected']}"
        assert "n_repriced" in data, "Missing n_repriced in summary"
        assert "n_no_tape" in data, "Missing n_no_tape in summary"
        assert "n_pending" in data, "Missing n_pending in summary"
        assert "pnl_is_repriced" in data, "Missing pnl_is_repriced in summary"


# ---------------------------------------------------------------------------
# 10. _update_pnl_by_wallet excludes ENTRY_REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUpdatePnlByWalletExcludesEntryRejected:
    """position_manager._update_pnl_by_wallet must exclude ENTRY_REJECTED."""

    def test_pnl_by_wallet_excludes_entry_rejected(self):
        from copytrade.models import CopytradePnlByWallet, CopytradePosition
        from copytrade.position_manager import _update_pnl_by_wallet

        cohort_id = "test-pnl-wallet"
        wallet = "wallettest" * 4

        entry_ts = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Create a winning position
        winner = CopytradePosition(
            cohort_id=cohort_id,
            mint=MINT_A,
            trigger_wallet=wallet,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            exit_reason="TP",
            entry_price=100.0,
            exit_price=120.0,
            sol_in=0.1,
            realized_pnl_sol=0.02,
            realized_pnl_pct=20.0,
        )
        winner.save()

        # Create an ENTRY_REJECTED position (should be excluded)
        rejected = CopytradePosition(
            cohort_id=cohort_id,
            mint=MINT_B,
            trigger_wallet=wallet,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=entry_ts,
            exit_ts=entry_ts,
            exit_reason="ENTRY_REJECTED",
            entry_price=100.0,
            exit_price=100.0,
            sol_in=0.1,
            realized_pnl_sol=None,
            realized_pnl_pct=None,
        )
        rejected.save()

        # Trigger the rollup via the winner position
        _update_pnl_by_wallet(winner)

        rollup = CopytradePnlByWallet.objects.get(cohort_id=cohort_id, address=wallet)
        assert rollup.n_trades == 1, f"n_trades should be 1 (excluding reject), got {rollup.n_trades}"
        assert rollup.win_rate == pytest.approx(1.0), f"win_rate should be 1.0, got {rollup.win_rate}"
        assert rollup.total_pnl_sol == pytest.approx(0.02), f"total_pnl_sol wrong: {rollup.total_pnl_sol}"


# ---------------------------------------------------------------------------
# 11. Trades view excludes ENTRY_REJECTED
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTradesViewExcludesEntryRejected:
    """copytrade_trades_view must exclude ENTRY_REJECTED from default log."""

    def test_trades_view_excludes_entry_rejected(self, client):
        from copytrade.models import CopytradeCohort, CopytradePosition, CopyTradeSettings

        cohort_id = "test-trades-view"
        cohort = CopytradeCohort(
            cohort_id=cohort_id,
            created_at=datetime(2023, 11, 15, 0, 0, 0, tzinfo=timezone.utc),
            active=True,
        )
        cohort.save()

        settings = CopyTradeSettings.get()
        settings.active_cohort_id = cohort_id
        settings.save()

        entry_ts = datetime(2023, 11, 15, 12, 0, 0, tzinfo=timezone.utc)
        exit_ts = datetime(2023, 11, 15, 12, 30, 0, tzinfo=timezone.utc)

        # Normal trade
        normal = CopytradePosition(
            cohort_id=cohort_id,
            mint=MINT_A,
            trigger_wallet="wallet1" * 3,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=entry_ts,
            exit_ts=exit_ts,
            exit_reason="TP",
            entry_price=100.0,
            exit_price=120.0,
            sol_in=0.1,
            realized_pnl_sol=0.02,
            realized_pnl_pct=20.0,
        )
        normal.save()

        # ENTRY_REJECTED trade — must NOT appear in trades view
        rejected = CopytradePosition(
            cohort_id=cohort_id,
            mint=MINT_B,
            trigger_wallet="wallet1" * 3,
            status=CopytradePosition.STATUS_CLOSED,
            mode=CopytradePosition.MODE_OBSERVE,
            entry_ts=entry_ts,
            exit_ts=entry_ts,
            exit_reason="ENTRY_REJECTED",
            entry_price=100.0,
            exit_price=100.0,
            sol_in=0.1,
            realized_pnl_sol=None,
            realized_pnl_pct=None,
        )
        rejected.save()

        response = client.get("/api/copytrade/trades/")
        assert response.status_code == 200, response.content
        data = response.json()

        trade_ids = [t["id"] for t in data["trades"]]
        assert normal.pk in trade_ids, "Normal TP trade should be in trades view"
        assert rejected.pk not in trade_ids, "ENTRY_REJECTED trade must NOT be in trades view"


# ---------------------------------------------------------------------------
# 12. copy_latency_s formula pin (frozen — must not change)
# ---------------------------------------------------------------------------

class TestCopyLatencyFormulaPin:
    """Pin the copy_latency_s formula so no one accidentally changes it.

    The formula is: copy_latency_s = arrival_epoch - float(block_time)
    where arrival_epoch = clock_arrival_ts.timestamp().

    This is intentionally ~1s over-read (integer block_time vs sub-second
    wall-clock arrival), and per the epic spec this is telemetry, NOT the
    repricing anchor.  DO NOT change this formula.
    """

    def test_copy_latency_formula_is_arrival_minus_block_time(self):
        """Verify honest_fill.check_copy_entry derives copy_latency_s = arrival - block_time."""
        from copytrade.honest_fill import check_copy_entry

        arrival = datetime(2023, 11, 15, 12, 0, 5, tzinfo=timezone.utc)  # epoch 1700049605
        block_time = 1700049600.0  # 5 seconds before arrival

        result = check_copy_entry(
            quote_price=100.0,
            fill_price=100.0,
            clock_arrival_ts=arrival,
            block_time=block_time,
        )
        # Should be ~5.0 seconds
        expected_latency = arrival.timestamp() - block_time
        assert result.copy_latency_s == pytest.approx(expected_latency, abs=1e-9), (
            f"copy_latency_s formula changed! Expected {expected_latency}, got {result.copy_latency_s}. "
            "DO NOT change this formula — it is intentionally arrival - block_time (telemetry only)."
        )

    def test_copy_latency_is_not_zero_when_arrival_after_block(self):
        """copy_latency_s must be positive when arrival is after block_time."""
        from copytrade.honest_fill import check_copy_entry

        arrival = datetime(2023, 11, 15, 12, 0, 2, tzinfo=timezone.utc)
        block_time = 1700049600.0  # 2 seconds before arrival

        result = check_copy_entry(
            quote_price=100.0,
            fill_price=100.0,
            clock_arrival_ts=arrival,
            block_time=block_time,
        )
        assert result.copy_latency_s is not None
        assert result.copy_latency_s > 0, "copy_latency_s should be positive for normal detection"

    def test_copy_latency_none_when_block_time_absent(self):
        """copy_latency_s must be None when block_time is not available."""
        from copytrade.honest_fill import check_copy_entry

        arrival = datetime(2023, 11, 15, 12, 0, 5, tzinfo=timezone.utc)

        result = check_copy_entry(
            quote_price=100.0,
            fill_price=100.0,
            clock_arrival_ts=arrival,
            block_time=None,  # no block_time
        )
        assert result.copy_latency_s is None, (
            "copy_latency_s must be None when block_time is absent (§8 P1 — no ambiguous imputation)"
        )
