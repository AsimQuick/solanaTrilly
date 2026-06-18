# ---
# module: copytrade.cohort_lifecycle
# sprint: sprint-12
# story: US-62 AC-62.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.models, copytrade.position_manager, copytrade.validators
# ---
"""SPEC §6 cohort fresh-start lifecycle: replace_cohort.

Uploading a new cohort JSON performs the SPEC §6 full REPLACEMENT in a single
atomic transaction:

    1. Auto-stop engine   — settings.engine_on = False
    2. Settle open positions — close_position(..., EXIT_SETTLE, settlement_price, settlement_ts)
    3. Purge              — DELETE all copytrade_* rows (pnl → positions → wallets → cohort)
    4. Validate + persist — validate_cohort_json, create CopytradeCohort(active=True)
    5. Subscribe wallets  — CopytradeWallet.objects.bulk_create(...)

Exactly ONE active cohort at a time; ZERO cross-cohort history retained (v1).

Constraints:
  - NEVER calls datetime.now() / datetime.utcnow() / time.time() — settlement_ts
    is injected by the caller.
  - Purge touches ONLY copytrade_* tables; the raw lake is untouched (§6.4.1).
  - All five steps run inside @transaction.atomic so a validation failure rolls
    back steps 1–3 without losing the old cohort data.

    Wait — validation failure AFTER purge would lose the old data.  To protect
    against this, validation is run FIRST (before any mutations) so the atomic
    block only executes mutations after the new JSON is confirmed good.
"""
from __future__ import annotations

from datetime import datetime, timezone

from django.db import transaction
from django.utils.dateparse import parse_date, parse_datetime

from copytrade.models import (
    CopytradeCohort,
    CopytradePnlByWallet,
    CopytradePosition,
    CopyTradeSettings,
    CopytradeWallet,
)
from copytrade.position_manager import close_position
from copytrade.validators import validate_cohort_json


def _parse_created_at(s: str) -> datetime:
    """Parse a created_at string to a timezone-aware datetime.

    Accepts ISO-8601 datetime strings (with or without timezone) and plain
    date strings (YYYY-MM-DD).  Raises ValueError when neither parses.
    """
    dt = parse_datetime(s)
    if dt is not None:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    d = parse_date(s)
    if d is not None:
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    raise ValueError(f"Cannot parse created_at: {s!r}")


def replace_cohort(
    new_cohort_json: dict,
    settlement_price: float,
    settlement_ts: datetime,
) -> CopytradeCohort:
    """Perform SPEC §6 full cohort REPLACEMENT.

    Validates the new JSON BEFORE touching any DB state so that a bad payload
    never leaves the system without an active cohort.

    Parameters
    ----------
    new_cohort_json:
        Parsed leaderboard.json dict (must pass US-58 validator).
    settlement_price:
        Price used to mark-close any currently open positions (injected clock).
    settlement_ts:
        Timestamp used for the settlement close (injected — never datetime.now()).

    Returns
    -------
    CopytradeCohort
        The newly persisted cohort row.

    Raises
    ------
    CohortJsonValidationError
        When new_cohort_json fails the US-58 schema validation.  No DB state
        is mutated when this is raised.
    """
    # --- Step 0: Validate FIRST — before any mutations ---
    validated = validate_cohort_json(new_cohort_json)

    with transaction.atomic():
        settings = CopyTradeSettings.get()

        # --- Step 1: Auto-stop engine ---
        settings.engine_on = False
        settings.save()

        # --- Step 2: Settle open positions ---
        open_positions = list(
            CopytradePosition.objects.filter(status=CopytradePosition.STATUS_OPEN)
        )
        for pos in open_positions:
            close_position(pos, CopytradePosition.EXIT_SETTLE, settlement_price, settlement_ts)

        # --- Step 3: Purge all copytrade_* records ---
        # Order: pnl_by_wallet → positions → wallets → cohort
        # (No FK constraints but logically dependent data first.)
        CopytradePnlByWallet.objects.all().delete()
        CopytradePosition.objects.all().delete()
        CopytradeWallet.objects.all().delete()
        CopytradeCohort.objects.all().delete()

        # --- Step 4: Persist new cohort ---
        created_at_dt = _parse_created_at(validated.created_at)
        cohort = CopytradeCohort.objects.create(
            cohort_id=validated.cohort_id,
            created_at=created_at_dt,
            description=validated.description,
            trade_config=validated.trade_config.model_dump(),
            active=True,
        )

        # Update settings: apply new trade_config and point to new cohort.
        tc = validated.trade_config
        settings.active_cohort_id = validated.cohort_id
        settings.mode = tc.mode
        settings.sol_size_per_trade = tc.sol_size_per_trade
        settings.take_profit_pct = tc.take_profit_pct
        settings.stop_loss_pct = tc.stop_loss_pct
        settings.exit_before_graduation = tc.exit_before_graduation
        settings.curve_completion_exit_pct = tc.curve_completion_exit_pct
        settings.max_hold_seconds = tc.max_hold_seconds
        settings.max_concurrent_positions = tc.max_concurrent_positions
        settings.copy_only_pumpfun_curve_buys = tc.copy_only_pumpfun_curve_buys
        settings.copy_first_buy_only = tc.copy_first_buy_only
        settings.dedupe_token_across_wallets = tc.dedupe_token_across_wallets
        settings.mirror_wallet_sells = tc.mirror_wallet_sells
        settings.save()

        # --- Step 5: Subscribe new wallets ---
        wallet_rows = [
            CopytradeWallet(
                cohort_id=validated.cohort_id,
                address=w.address,
                rank=w.rank,
                precision=w.precision,
                median_lead_min=w.median_lead_min,
            )
            for w in validated.wallets
        ]
        CopytradeWallet.objects.bulk_create(wallet_rows)

    return cohort
