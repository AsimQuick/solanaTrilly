# ---
# module: copytrade.cohort_lifecycle
# sprint: sprint-12, copytrade-2.1-loader
# story: US-62 AC-62.1, copytrade-v2.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
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
from copytrade.validators import validate_cohort_json, validate_cohort_v2, validate_cohort_v2_1


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


def replace_cohort_v2(
    new_cohort_json: dict,
    settlement_price: float,
    settlement_ts: datetime,
) -> CopytradeCohort:
    """SPEC §6 full cohort REPLACEMENT for the `copytrade-2.0` contract.

    Same fresh-start discipline as ``replace_cohort`` (validate-first, then a
    single atomic stop -> settle -> purge -> persist -> subscribe), adapted to the
    two-head 2.0 cohort:

      - the full cohort dict (``global`` + ``strategies``) is stored verbatim in
        ``CopytradeCohort.trade_config`` (the audit record + the per-head exit
        source the engine reads at runtime),
      - ``CopyTradeSettings`` is updated with the engine-wide ``global`` knobs
        (mode / usd_size_per_trade / min_trigger_buy_usd / pump_fun_only /
        max_concurrent_positions / copy_first_buy_only / dedupe), and
      - each wallet row is tagged with its ``strategy_id`` (which head owns it),
        so a trigger resolves the right exit.

    ``mirror_wallet_sells`` on the settings row stays False — mirror-sell is a
    PER-HEAD exit (consistent_scalp) read from ``strategies``, not a global flag.

    Validates BEFORE any mutation so a bad payload never leaves the system
    without an active cohort.  ``settlement_ts`` is injected (never datetime.now()).
    """
    cohort = validate_cohort_v2(new_cohort_json)

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
        CopytradePnlByWallet.objects.all().delete()
        CopytradePosition.objects.all().delete()
        CopytradeWallet.objects.all().delete()
        CopytradeCohort.objects.all().delete()

        # --- Step 4: Persist new cohort (full 2.0 dict verbatim) ---
        created_at_dt = _parse_created_at(cohort.created_at)
        cohort_row = CopytradeCohort.objects.create(
            cohort_id=cohort.cohort_id,
            created_at=created_at_dt,
            description=cohort.description,
            trade_config=new_cohort_json,
            active=True,
        )

        # Apply the engine-wide `global` knobs to the settings singleton.
        g = cohort.global_
        settings.active_cohort_id = cohort.cohort_id
        settings.mode = g.mode
        settings.usd_size_per_trade = g.usd_size_per_trade
        settings.min_trigger_buy_usd = g.trigger.min_trigger_buy_usd
        settings.pump_fun_only = g.trigger.pump_fun_only
        settings.max_concurrent_positions = g.max_concurrent_positions
        settings.copy_first_buy_only = g.trigger.copy_first_buy_only
        settings.dedupe_token_across_wallets = g.trigger.dedupe_token_across_wallets
        settings.mirror_wallet_sells = False  # per-head exit, not a global flag
        settings.save()

        # --- Step 5: Subscribe new wallets, tagged with their head ---
        # Dedupe by address (a wallet in two enabled heads is subscribed ONCE;
        # the FIRST head in declaration order owns it — the cross-head dedupe rule).
        wallet_rows = []
        seen: set[str] = set()
        for strat in cohort.enabled_strategies():
            for w in strat.wallets:
                if w.address in seen:
                    continue
                seen.add(w.address)
                wallet_rows.append(
                    CopytradeWallet(
                        cohort_id=cohort.cohort_id,
                        address=w.address,
                        rank=w.rank,
                        strategy_id=strat.id,
                    )
                )
        CopytradeWallet.objects.bulk_create(wallet_rows)

    return cohort_row


def replace_cohort_v2_1(
    new_cohort_json: dict,
    settlement_price: float,
    settlement_ts: datetime,
) -> CopytradeCohort:
    """SPEC §6 full cohort REPLACEMENT for the `copytrade-2.1` contract.

    Schema 2.1 is a single graded watchlist (≤20 wallets, per-wallet `style` exit).
    The fresh-start discipline mirrors ``replace_cohort_v2`` exactly:
    validate-first, then atomic stop -> settle -> purge -> persist -> subscribe.

    Key differences from 2.0:
      - No strategies[] array; the wallet list IS the single "strategy".
      - Each wallet carries a `style` tag that is persisted on CopytradeWallet.style
        (migration 0006).  At runtime, style=="ride" -> our_trailing exit;
        every other style -> mirror_wallet_sell (global.exit.default).
      - strategy_id on CopytradeWallet is set to the wallet's style value ("ride" /
        "scalp") so the existing engine's wallet_to_strategy lookup continues to
        work transparently — it just returns the style instead of a head id.
      - The ride exit config is constructed from global.exit.ride_tagged and stored
        in the CopytradeCohort.trade_config verbatim dict for the engine to read.

    ``mirror_wallet_sells`` on the settings row stays False (per-wallet exit, not
    a global flag).  ``settlement_ts`` is injected (never datetime.now()).
    """
    cohort = validate_cohort_v2_1(new_cohort_json)

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
        CopytradePnlByWallet.objects.all().delete()
        CopytradePosition.objects.all().delete()
        CopytradeWallet.objects.all().delete()
        CopytradeCohort.objects.all().delete()

        # --- Step 4: Persist new cohort (full 2.1 dict verbatim) ---
        created_at_dt = _parse_created_at(cohort.created_at)
        cohort_row = CopytradeCohort.objects.create(
            cohort_id=cohort.cohort_id,
            created_at=created_at_dt,
            description=cohort.description,
            trade_config=new_cohort_json,
            active=True,
        )

        # Apply the engine-wide `global` knobs to the settings singleton.
        g = cohort.global_
        settings.active_cohort_id = cohort.cohort_id
        settings.mode = g.mode
        settings.usd_size_per_trade = g.usd_size_per_trade
        settings.min_trigger_buy_usd = g.trigger.min_trigger_buy_usd
        settings.pump_fun_only = g.trigger.pump_fun_only
        settings.max_concurrent_positions = g.max_concurrent_positions
        settings.copy_first_buy_only = g.trigger.copy_first_buy_only
        settings.dedupe_token_across_wallets = g.trigger.dedupe_token_across_wallets
        settings.mirror_wallet_sells = False  # per-wallet exit, not a global flag
        settings.save()

        # --- Step 5: Subscribe wallets, persisting rank + style ---
        # strategy_id is set to the wallet's style so the existing engine lookup
        # (wallet_to_strategy -> strategy_id -> exit_by_strategy) works without
        # modification: EngineState.wallet_to_strategy maps address -> style,
        # and EngineState.exit_by_strategy maps style -> exit config.
        wallet_rows = [
            CopytradeWallet(
                cohort_id=cohort.cohort_id,
                address=w.address,
                rank=w.rank,
                strategy_id=w.style,   # reuse existing field; style IS the "strategy"
                style=w.style,         # new 2.1 field — explicit style for introspection
            )
            for w in cohort.wallets
        ]
        CopytradeWallet.objects.bulk_create(wallet_rows)

    return cohort_row
