# ---
# module: copytrade.diagnostics
# sprint: sprint-15
# story: US-80
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-23
# dependencies: none
# ---
"""Diagnostic and validation probes for the curvestage copy-trade pipeline.

Modules:
    gate_fidelity  — recompute all three copy gates per pick at the buy instant
    resettle       — honest re-settle using the cum-vol_sol>=85 graduation label
"""
