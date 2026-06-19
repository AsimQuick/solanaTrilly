# ---
# module: tools.firehose_state
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: os, sys, django, core.models.PipelineState
# ---
"""firehose_state.py — idempotent toggle for PipelineState.firehose_active.

This is the operator's ON/OFF switch for the live firehose daemon (run_firehose).
It ONLY touches ``PipelineState.firehose_active`` — it NEVER touches
``scoring_enabled`` or ``trading_enabled`` (capital safety: trading stays off).

NOTE on naming: the existing ``tools/firehose_activate.py`` is the Birdeye
SUBSCRIBE_TXS golden-capture activation tool (a different concern, with its own
AC-22.2 test).  This file is the PipelineState flag toggle and is deliberately
named ``firehose_state`` to avoid clobbering that tool.

The desired state is resolved (in priority order):
  1. an explicit argument: ``on`` | ``off`` | ``status``
  2. the ``FIREHOSE_STATE`` env var: ``on`` | ``off`` (any other value => status)
  3. default: ``status`` (print only, mutate nothing)

USAGE
=====
As a Django management command (preferred; see core/management/commands/firehose_state.py):
    docker compose run --rm web python manage.py firehose_state on
    docker compose run --rm web python manage.py firehose_state off
    docker compose run --rm web python manage.py firehose_state status

Via ``manage.py shell <`` with the state in an env var (idempotent):
    docker compose run --rm -e FIREHOSE_STATE=on  web python manage.py shell < tools/firehose_state.py
    docker compose run --rm -e FIREHOSE_STATE=off web python manage.py shell < tools/firehose_state.py

As a bare script (DJANGO_SETTINGS_MODULE must be set):
    FIREHOSE_STATE=on python tools/firehose_state.py

Idempotent: setting ON when already ON (or OFF when already OFF) is a no-op that
still prints the resulting state.  Exit code 0 on success.
"""
from __future__ import annotations

import os
import sys

_ON_VALUES = {"on", "true", "1", "yes", "enable", "enabled"}
_OFF_VALUES = {"off", "false", "0", "no", "disable", "disabled"}


def _ensure_django() -> None:
    """Defensively configure Django (``manage.py shell <`` already calls setup())."""
    import django
    from django.apps import apps

    if not apps.ready:
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
        django.setup()


def resolve_desired_state(argv: list[str] | None, env: dict | None = None) -> str | None:
    """Resolve the desired action from argv then env.

    Pure function (no Django) for unit-testability.

    Returns:
        "on"     — set firehose_active True
        "off"    — set firehose_active False
        None     — status only (print, mutate nothing)
    """
    env = env if env is not None else os.environ
    # 1. explicit positional arg
    tokens = [a.strip().lower() for a in (argv or []) if a and not a.startswith("-")]
    for tok in tokens:
        if tok in _ON_VALUES:
            return "on"
        if tok in _OFF_VALUES:
            return "off"
        if tok == "status":
            return None
    # 2. env var
    raw = (env.get("FIREHOSE_STATE") or "").strip().lower()
    if raw in _ON_VALUES:
        return "on"
    if raw in _OFF_VALUES:
        return "off"
    # 3. default: status only
    return None


def apply_state(desired: str | None) -> bool:
    """Apply *desired* to the PipelineState singleton and print the result.

    NEVER touches scoring_enabled or trading_enabled.

    Args:
        desired: "on" | "off" | None (status only).

    Returns:
        The resulting ``firehose_active`` boolean.
    """
    from core.models import PipelineState

    state = PipelineState.get()  # creates pk=1 with safe False defaults if absent

    if desired == "on" and not state.firehose_active:
        state.firehose_active = True
        state.save(update_fields=["firehose_active"])
    elif desired == "off" and state.firehose_active:
        state.firehose_active = False
        state.save(update_fields=["firehose_active"])

    state.refresh_from_db()
    action = "STATUS" if desired is None else f"SET {desired.upper()}"
    print(
        f"[firehose_state] {action} -> "
        f"firehose_active={state.firehose_active} "
        f"(scoring_enabled={state.scoring_enabled}, "
        f"trading_enabled={state.trading_enabled} — untouched)"
    )
    return state.firehose_active


def main(argv: list[str] | None = None) -> int:
    _ensure_django()
    desired = resolve_desired_state(argv if argv is not None else sys.argv[1:])
    apply_state(desired)
    return 0


# Run on import too, so `python manage.py shell < tools/firehose_state.py` works.
# Under `manage.py shell <`, sys.argv is the shell's argv (no useful positional),
# so the env-var path (FIREHOSE_STATE) is the canonical driver in that mode.
if __name__ == "__main__":
    sys.exit(main())
else:  # executed via `manage.py shell <` (module name == "__builtin__"/"builtins")
    # Only auto-run under the shell-redirect entry (not on a normal `import`).
    if os.environ.get("FIREHOSE_STATE") is not None:
        try:
            _ensure_django()
            apply_state(resolve_desired_state([]))
        except Exception as exc:  # pragma: no cover - shell-redirect convenience path
            print(f"[firehose_state] error: {exc}")
