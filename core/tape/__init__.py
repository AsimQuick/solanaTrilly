# ---
# module: core.tape
# sprint: sprint-5
# story: US-18 AC-18.1, US-20 AC-20.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.recorder, core.tape.reconciler
# ---
"""tape — the PumpSwap swap-tape recorder package (PRD §6.2).

The recorder core lives here.  All modules in this package depend ONLY on the
abstract DataSource and Clock interfaces (Principle #7 / PRD §4).  No module
may import LiveSource or ReplaySource; no module may call datetime.now() or
time.time() directly.
"""
