# ---
# module: copytrade.curvestage_engine
# epic: EPIC-copy-curvestage-integration
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# dependencies: copytrade.{entry_features,pgrad_classifier,birdeye_tape,curvestage_settle,buy_trigger,models}
# ---
"""Curve-stage cohort gate + honest-PnL settler (self-contained, observe-only).

A parallel path to the existing curve-fill engine: a watched pool-wallet's ≥$250
first-buy of a pump.fun token is a discovery trigger; we copy (observe $25) only
if ALL gates hold, and settle the realized PnL by reconstructing the honest entry
+ ride-to-grad exit from Birdeye (``curvestage_settle.settle_grad``) so the soak's
PnL matches the offline number.

GATES (at the buy instant, all live-observable, leak-safe):
  1. on_curve — the watched buy is on the bonding curve (not migrated). [event program]
  2. curve_frac = pre_sol_in/85 ≤ 0.60.                                   [Birdeye tape -> entry_features]
  3. P(graduate) ≥ frozen_threshold (top-25% surrogate).                  [seed LGBM]

ISOLATION: curvestage positions are NOT tracked in the engine's in-memory
``state.open_positions`` and are NOT managed by ``manage_positions`` — their full
lifecycle (open at the gate -> settle at graduation) lives in the DB + this module
+ the periodic settler.  The existing cohort path is untouched.

BLOCKING: ``handle_curvestage_buy`` runs inside ``handle_event`` (already off the
event loop via sync_to_async) and the settler runs in a Celery worker — both can
make the blocking Birdeye fetch safely (the #377 lesson: never on the event loop).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from copytrade.birdeye_tape import fetch_token_tape
from copytrade.buy_trigger import is_on_bonding_curve
from copytrade.curvestage_settle import settle_grad
from copytrade.entry_features import birdeye_items_to_owner_tape, entry_features
from copytrade.models import CopytradePosition
from copytrade.pgrad_classifier import get_pgrad_classifier

logger = logging.getLogger(__name__)

#: cohort-id marker that activates the curvestage gate path.
CURVESTAGE_TAG = "curvestage"

#: Pre-grad lookback for the entry-features tape (matches the lab 1h window).
_PREGRAD_LOOKBACK_S = 3600
#: Outcome window: a token that has not graduated within this is settled as a
#: non-grad loss (rides to tape_end), matching the lab's ride-to-grad fallback.
_OUTCOME_WINDOW_S = 1800
#: Post-grad VWAP needs the first 30s after gts — fetch a little past it.
_POSTGRAD_PAD_S = 90


def is_curvestage_cohort(cohort_id: str) -> bool:
    return bool(cohort_id) and CURVESTAGE_TAG in cohort_id


def handle_curvestage_buy(
    event, sol_usd: float, cohort_id: str, strategy_id: str,
) -> Optional[CopytradePosition]:
    """Evaluate the 3 curvestage gates on a watched ≥$250 pump buy; book observe $25.

    Returns the booked CopytradePosition on a full pass, or None (gate fail /
    dedupe / no tape).  Fail-closed: any missing tape or unscorable token is skipped
    (we never guess a score).  Entry PnL is set later by the settler (authoritative).
    """
    mint = event.mint
    buy_ts = int(event.block_time)

    # Gate 1 — on the bonding curve at the buy (not a post-grad AMM buy).
    if not is_on_bonding_curve(event):
        return None

    # First-buy / cross-wallet dedupe: one curvestage position per mint.
    if CopytradePosition.objects.filter(
        cohort_id=cohort_id, mint=mint, status=CopytradePosition.STATUS_OPEN
    ).exists():
        return None

    # Gate 1b — authoritative POST-grad reject via the RECORDER's graduation time.
    # is_on_bonding_curve() above defaults to True when the Helius wallet event
    # carries no `program` field (these events never do), so a POST-grad buy slips
    # the cheap gate and would settle as a spurious loss (proven live: position
    # DoeeM6LF — the watched buy was 66s AFTER graduation).  The recorder's
    # graduated_block_time is the reliable signal: if the token had already
    # graduated at/before the buy, this is not a curve-stage entry — skip.
    gts = _graduation_bt(mint)
    if gts is not None and gts <= buy_ts:
        logger.info(
            "[curvestage] mint=%.8s POST-grad buy (grad_bt=%d <= buy_ts=%d) — skip (not on curve).",
            mint, gts, buy_ts,
        )
        return None

    # Fetch the token's pre-grad tape from Birdeye (same basis as training).
    items = fetch_token_tape(mint, buy_ts - _PREGRAD_LOOKBACK_S, buy_ts)
    if not items:
        logger.info("[curvestage] mint=%.8s no Birdeye tape at buy — skip (fail-closed).", mint)
        return None
    otr = birdeye_items_to_owner_tape(items)
    trigger_usd = float(event.sol_amount) * float(sol_usd)
    feats = entry_features(otr, buy_ts, gts=None, wallet_buy_usd=trigger_usd)
    if feats is None:
        return None

    # Gate 2 — curve room.
    if feats["curve_frac"] > 0.60:
        return None

    # Gate 3 — P(graduate) top-25% surrogate (frozen threshold).
    clf = get_pgrad_classifier()
    passed, pgrad = clf.passes(feats)
    if not passed:
        logger.info(
            "[curvestage] mint=%.8s gate3 fail pg=%.4f<thr=%.4f curve_frac=%.3f — skip.",
            mint, pgrad, clf.threshold, feats["curve_frac"],
        )
        return None

    # Book the observe position.  entry_price/PnL are filled by the settler.
    entry_ts = datetime.fromtimestamp(buy_ts, tz=timezone.utc)
    size_usd = 25.0
    sol_in = size_usd / sol_usd if sol_usd > 0 else 0.0
    pos = CopytradePosition.objects.create(
        cohort_id=cohort_id, mint=mint, trigger_wallet=event.wallet, strategy_id=strategy_id,
        status=CopytradePosition.STATUS_OPEN, mode=CopytradePosition.MODE_OBSERVE,
        entry_ts=entry_ts, size_usd=size_usd, sol_in=sol_in,
    )
    logger.info(
        "[curvestage] OPEN observe mint=%.8s wallet=%.8s pg=%.4f curve_frac=%.3f trigger_usd=%.0f",
        mint, event.wallet, pgrad, feats["curve_frac"], trigger_usd,
    )
    return pos


def _graduation_bt(mint: str) -> Optional[int]:
    """Read the recorder's graduation epoch for *mint* (None if not graduated).

    A read-only cross-reference into the recorder's tokens table; the §5 isolation
    rule is about copytrade TABLES not declaring FKs into the lake, not about reads.
    """
    try:
        from core.models import Token  # noqa: PLC0415

        tok = Token.objects.filter(mint=mint).values("graduated_block_time").first()
        if tok and tok["graduated_block_time"]:
            return int(tok["graduated_block_time"])
    except Exception as exc:  # noqa: BLE001 — never let a read miss break settlement
        logger.debug("[curvestage] graduation lookup failed mint=%.8s (%s)", mint, exc)
    return None


def settle_due_curvestage_positions(now: datetime, *, cohort_id: str) -> list[CopytradePosition]:
    """Settle open curvestage positions whose token graduated OR whose window elapsed.

    For each: source the token tape from Birdeye over [buy-1h, (gts or buy+window)+pad]
    (ONE basis, USD), reconstruct the honest entry + ride-to-grad exit via
    ``settle_grad``, and write the authoritative realized PnL + EXIT_GRADUATION (or
    EXIT_TIMER for a non-grad loss; EXIT_ENTRY_REJECTED for an unsettleable fill).
    """
    closed: list[CopytradePosition] = []
    open_qs = CopytradePosition.objects.filter(
        cohort_id=cohort_id, status=CopytradePosition.STATUS_OPEN
    )
    for pos in open_qs:
        if pos.entry_ts is None:
            continue
        buy_ts = int(pos.entry_ts.replace(tzinfo=timezone.utc).timestamp())
        gts = _graduation_bt(pos.mint)
        elapsed = (now - pos.entry_ts).total_seconds()
        if gts is None and elapsed < _OUTCOME_WINDOW_S:
            continue  # not graduated yet and still within the outcome window — wait.

        t_to = (gts if gts else buy_ts + _OUTCOME_WINDOW_S) + _POSTGRAD_PAD_S
        items = fetch_token_tape(pos.mint, buy_ts - _PREGRAD_LOOKBACK_S, t_to)
        if not items:
            # No tape to settle on yet (indexing lag) — retry next tick unless the
            # window is far past (then close VOID so it doesn't loop forever).
            if elapsed > _OUTCOME_WINDOW_S + 3600:
                _close(pos, CopytradePosition.EXIT_VOID, None, None, now)
                closed.append(pos)
            continue
        otr = birdeye_items_to_owner_tape(items)
        res = settle_grad(otr, buy_ts, gts)
        if res["reason"] != "ok":
            # Honest fill not realizable (no_fill/badfill/slip6002) — exclude from
            # the soak metric (NULL PnL), matching the offline population.
            _close(pos, CopytradePosition.EXIT_ENTRY_REJECTED, None, None, now)
            closed.append(pos)
            continue
        pnl_pct = res["pnl_pct"]
        sol_in = float(pos.sol_in or 0.0)
        pnl_sol = sol_in * pnl_pct / 100.0
        reason = CopytradePosition.EXIT_GRADUATION if res["graduated"] else CopytradePosition.EXIT_TIMER
        exit_ts = datetime.fromtimestamp(res["exit_t"], tz=timezone.utc)
        pos.entry_price = res["entry"]
        _close(pos, reason, pnl_pct, pnl_sol, now, exit_ts=exit_ts)
        closed.append(pos)
        logger.info(
            "[curvestage] SETTLE mint=%.8s %s pnl=%.1f%% (grad=%s)",
            pos.mint, reason, pnl_pct, res["graduated"],
        )
    return closed


def _close(pos, reason, pnl_pct, pnl_sol, now, *, exit_ts=None) -> None:
    pos.status = CopytradePosition.STATUS_CLOSED
    pos.exit_reason = reason
    pos.exit_ts = exit_ts or now
    pos.realized_pnl_pct = pnl_pct
    pos.realized_pnl_sol = pnl_sol
    pos.save(update_fields=[
        "status", "exit_reason", "exit_ts", "realized_pnl_pct", "realized_pnl_sol", "entry_price",
    ])
