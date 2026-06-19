# ---
# module: core.pricing
# sprint: cutover (copy-trade live)
# story: shared-sol-usd-spot
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: none
# ---
"""Shared pricing helpers used by BOTH trading heads (prediction + copy-trade).

Currently exposes the cached SOL/USD spot (``core.pricing.sol_usd``) that
replaces the hardcoded ``sol_usd = 140.0`` formerly inlined in the prediction
paper path — a single source of USD valuation for both the model pipeline's
paper sizing and the copy-trade engine's ``min_trigger_buy_usd`` gate.
"""
