# EPIC — Trustworthy Copy Paper PnL: Reprice Observe Fills from the Firehose Tape

**Status:** APPROVED (tester GO + delta, 2026-06-21) — operator-directed
**Workflow:** live hotfix/epic-PR (NOT sprint ceremony)
**Authorization:** copy track is normally hands-off; operator directed this change explicitly.

---

## Problem

Paper/observe copy-trade prices ENTRY from a simulated bonding-curve read
(`read_curve_state` → `simulate_buy`, `copytrade/engine_runtime.py::handle_event`). On fast tokens
that read is stale and too-cheap, so losing trades look like big winners. The dashboard shows
~**+2.0 SOL** this week, but reconstructing those exact trades against the REAL on-chain trades in the
firehose lake gives **~+0.1 SOL (break-even)** — the "winners" were fake-cheap entries on tokens that
had already run too far to actually buy. The Copy-Trade dashboard PnL is **not trustworthy** until the
observe path prices fills from the firehose tape.

(Ghost-buy #364 only corrects the LIVE send path, not observe/paper.)

## Fix (operator spec — do NOT exceed)

Reprice BOTH legs of observe/paper positions from the firehose lake
(`lake/firehose/dt=YYYY-MM-DD/part-0.jsonl.gz`; rows: mint, block_time, slot, signature, price,
side, vol_sol):
- ENTRY price = price of the next real trade ~1.5s after the wallet-buy detection (see WINDOW RULE).
- SKIP (ENTRY_REJECTED, never entered) if that price is >15% above the wallet's price
  (`event.raw["price"]`) — SAME cap as the live honest-fill path.
- EXIT price = next real trade after the exit signal, same window rule.
- **Do NOT build heavier:** no own-impact, no exact-ordering, no side/size/signer filtering
  ("next real trade" = first row in window regardless of side/size/signer), no sleep in
  `handle_event`, no live-exec changes, no historical backfill, no latency optimization. The residual
  uncertainty is settled by the small real-SOL ghost-buy test.

## Integration point (chosen): retrospective repricing at SETTLEMENT

NOT a live 1.5s sleep (blocks the single-threaded consumer; lake write-lag > 1.5s anyway). Book the
provisional curve-sim entry exactly as today, then re-price at settlement when the lake has flushed:
- New `copytrade/fill_repricing.py`: `find_next_trade_price(...)` + `reprice_position(pk, lake_base_dir)`.
- Celery task `reprice_copy_fill` dispatched from `copytrade/position_manager.py::close_position`
  (wrap `.delay()` in a bare except → never crash close_position). Runs on celery-worker (mounts lake).
- Reuse `core/tape/lake_reader.py` `LakeReader(base_dir="lake/firehose")` — no new lake abstraction.
- New column `CopytradePosition.entry_reprice_status` (CharField,nullable):
  `"REPRICED" | "ENTRY_REJECTED_TAPE" | "NO_TAPE"`. One migration.

## WINDOW RULE (the load-bearing delta — reproduce the lab's break-even)

Lake `block_time` is **integer-second** (`tape_sink.py:131` `int(raw_bt)`). The lab reconstructed at
0.5/1/2s latency and got the SAME (break-even) PnL at every speed → row selection collapses to blocks.
Deterministic rule:

```
wallet_block_time_int = int(event.block_time)
candidate = first firehose row for mint where
    int(row["block_time"]) >= wallet_block_time_int + REPRICE_BLOCK_OFFSET
within [wallet_block_time_int + REPRICE_BLOCK_OFFSET,
        wallet_block_time_int + REPRICE_BLOCK_OFFSET + REPRICE_WINDOW_S]
EXCLUDE same-block rows (block_time == wallet_block_time may be prior txs in that block).
No side/size filter. Return None → NO_TAPE (keep curve-sim price, do NOT reject).
```
Module-level constants (NOT DB config — methodology params): `REPRICE_BLOCK_OFFSET = 1`,
`REPRICE_WINDOW_S = 10`. Scan the wallet's UTC date AND the next date (10s window can cross midnight).

**⚠️ The offset that exactly reproduces the lab's +0.1 is an INFERENCE.** Default block_offset=1 is the
best guess; if solanatrills can supply the exact reconstruction rule, set the constant to match. The
direction (inflated → break-even) holds regardless because we use REAL tape prices.

## Latency — frozen, out of scope (correction 3)

`copy_latency_s` over-reads by up to ~1s (integer block_time vs sub-second arrival). **Do NOT "fix" it**
— it's telemetry, not the repricing anchor (anchor = `int(event.block_time)`). Reconstruction showed
PnL break-even at 0.5/1/2s → the cost of copying is being the SECOND buyer; no speed within the
slippage cap fixes it. NO geyser/colo/Django-rebuild for copy speed. (Latency may still matter for the
MODEL pipeline — not here.)

## Dashboard surfacing (corrections 1 & 2)

**Win-rate denominator** — exclude `ENTRY_REJECTED` (never bought, lost nothing; moves 32% → 47%):
- `copytrade/api.py::copytrade_summary_view`: `.exclude(exit_reason=EXIT_ENTRY_REJECTED)` on the
  trade/win-rate queryset; add `n_rejected` count.
- `copytrade/api.py::copytrade_trades_view`: exclude ENTRY_REJECTED from default trades log.
- `copytrade/position_manager.py::_update_pnl_by_wallet`: add the same `.exclude(...)`.
**PnL trustworthiness** — summary also returns `n_repriced`, `n_no_tape`, `n_pending`,
`pnl_is_repriced` (= all non-rejected closed are REPRICED). NO_TAPE counts as NOT-trusted.
**Frontend** (`frontend/src/CopyTradeTab.jsx`): Net-PnL cell shows yellow `(curve-sim — not yet
repriced)` when `pnl_is_repriced=false`; add a chip `"N signals skipped (slippage)"` when
`n_rejected>0`. No FE PnL math change (reads API values).

## Showstoppers (must close in-PR)
1. **`copytrade_engine` does NOT mount the lake.** Add `- solanatrilly_lake:/app/lake/firehose` to the
   `copytrade_engine` volumes in `docker-compose.staging.yml`, else repricing is silently inoperative
   in prod. (The settler runs on celery-worker, which DOES mount it — but verify; if the dispatch path
   ever reads the lake from copytrade_engine, the mount is required there too.)
2. **Price-unit basis.** Confirm the lake row `price` units match `event.raw["price"]`
   (lamports vsol/vtok) BEFORE the 15% cap, or every entry falsely rejects (the prior −30% phantom-slip
   bug). Inspect the Birdeye swap mapper that populates the lake `price`. Convert if needed.

## Build scope
**New:** `copytrade/fill_repricing.py`; Celery task `reprice_copy_fill`; migration for
`entry_reprice_status`; tests `copytrade/tests/test_fill_repricing.py`.
**Modify:** `copytrade/position_manager.py` (dispatch + _update_pnl_by_wallet exclude),
`copytrade/api.py` (summary + trades), `frontend/src/CopyTradeTab.jsx` (banner + chip),
`docker-compose.staging.yml` (lake mount).
**Frozen (no change):** `honest_fill.py` (copy_latency_s formula), `engine_runtime.py`,
`core/firehose/tape_sink.py`, `core/tape/lake_reader.py`, live-exec path.

## DoD
1. `find_next_trade_price` selects by `int(block_time) >= int(wallet_block_time)+REPRICE_BLOCK_OFFSET`
   (block-based, not sub-second float); same-block excluded; constants at module level.
2. Settler dispatched at close (guarded); migration clean; `entry_reprice_status` populated.
3. `copytrade_engine` mounts the lake in compose.
4. Price-unit basis verified before the cap.
5. Summary API returns n_rejected/n_repriced/n_no_tape/n_pending/pnl_is_repriced; total_trades +
   win_rate exclude ENTRY_REJECTED; trades view excludes them.
6. FE banner + "N signals skipped (slippage)" chip.
7. Full suite green; copy_latency_s formula untouched (pin with a test).
8. Staging: after a position closes, `logs copytrade_engine|celery-worker | grep reprice` shows
   settler activity; repriced PnL direction matches the inflated→break-even finding.

## Test plan (delta highlights)
- `test_find_next_trade_price_uses_integer_block_offset` — pins the block-offset rule (row at
  wallet_bt+1 selected, same-block excluded).
- `test_find_next_trade_price_excludes_same_block`; `..._crosses_midnight`; `..._empty_window`→None.
- `test_reprice_position_within_cap` / `_cap_exceeded` (ENTRY_REJECTED_TAPE) / `_no_tape` (keeps sim).
- `test_paper_week_pnl_direction` — repriced net PnL is negative/break-even vs the inflated booked
  number (locks the +2.0→~+0.1 direction).
- `test_summary_win_rate_excludes_entry_rejected` (2 wins + 1 reject → total=2, win=1.0, n_rejected=1);
  `test_pnl_by_wallet_excludes_entry_rejected`; `test_trades_view_excludes_entry_rejected`.
- Pin `copy_latency_s` formula test (fails if anyone changes it).
