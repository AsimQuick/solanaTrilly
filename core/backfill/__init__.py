# ---
# module: core.backfill
# sprint: epic-tape-sourcing-escalation
# story: EPIC-tape-sourcing-escalation Tier 2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: none
# ---
"""Tape-sourcing escalation backfill package.

Tier 2: lake_backfill.LakeBackfiller — reads pre-grad swaps from the
firehose lake (local, free, no external calls).

Tier 3: birdeye_trade_history (follow-on PR) — Birdeye REST fallback
when the lake has no rows for the mint.
"""
