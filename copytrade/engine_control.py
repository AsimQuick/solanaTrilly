# ---
# module: copytrade.engine_control
# sprint: sprint-12
# story: US-62 AC-62.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: copytrade.models
# ---
"""Copytrade engine ON/OFF runtime toggle (SPEC §2 / §5).

engine_on in CopyTradeSettings is RUNTIME state (not in the JSON).
It gates ONLY the copytrade engine — the firehose, graduation feed, and model
pipeline keep running regardless (§5 isolation).

set_engine_on() is the single public entry point for toggling this flag.
It imports ONLY from copytrade.* — zero core.* imports enforce the §5 isolation
boundary at the module level.
"""

from copytrade.models import CopyTradeSettings


def set_engine_on(value: bool) -> CopyTradeSettings:
    """Toggle the copytrade engine ON (True) or OFF (False).

    Updates CopyTradeSettings.engine_on ONLY — no PipelineState/PipelineConfig
    mutation, no firehose/scoring/trading flag changes (SPEC §5 isolation).

    Returns the updated singleton CopyTradeSettings row.
    """
    settings = CopyTradeSettings.get()
    settings.engine_on = bool(value)
    settings.save()
    return settings
