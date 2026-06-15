# ---
# module: core.tape
# sprint: sprint-5
# story: US-18 AC-18.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: core.tape.recorder
# ---
"""tape — the PumpSwap swap-tape recorder package (PRD §6.2).

The recorder core lives here.  All modules in this package depend ONLY on the
abstract DataSource and Clock interfaces (Principle #7 / PRD §4).  No module
may import LiveSource or ReplaySource; no module may call datetime.now() or
time.time() directly.
"""
