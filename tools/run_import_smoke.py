# ---
# file: tools/run_import_smoke.py
# project: solanatrilly
# purpose: Pre-deploy in-container import smoke against the built staging image.
#          Reads symbol list from ops/import_smoke_symbols.json and runs a
#          `docker compose run --rm web python3 -c "..."` check that starts with
#          `import django; django.setup();` to avoid sprint-13-class import errors.
# story: US-71 AC-71.1
# sprint: sprint-14
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: ops/import_smoke_symbols.json
# ---

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
_SYMBOLS_FILE = _REPO_ROOT / "ops" / "import_smoke_symbols.json"


def load_symbols() -> list[dict]:
    """Read and return the symbols list from ops/import_smoke_symbols.json."""
    with _SYMBOLS_FILE.open() as fh:
        data = json.load(fh)
    return data["symbols"]


def build_smoke_command(symbols: list[dict]) -> str:
    """Return a python -c string for the import smoke.

    The string starts with ``import django; django.setup();`` so that Django's
    app registry is initialised before any model-touching modules are imported
    (guards against the sprint-13 class-at-import-time failure mode).
    """
    parts = ["import django; django.setup()"]
    for sym in symbols:
        parts.append(f"from {sym['module']} import {sym['attr']}")
    attrs = ", ".join(sym["attr"] for sym in symbols)
    parts.append(f"print('AC-71.1 import smoke OK: {attrs}')")
    return "; ".join(parts)


def run_smoke(compose_project: str | None = None) -> int:
    """Run the import smoke inside the built web container.

    Args:
        compose_project: optional ``-p PROJECT`` value for docker compose.

    Returns:
        The subprocess exit code (0 = pass, non-zero = fail).
    """
    symbols = load_symbols()
    cmd_str = build_smoke_command(symbols)

    docker_cmd: list[str] = ["docker", "compose"]
    if compose_project:
        docker_cmd += ["-p", compose_project]
    docker_cmd += ["run", "--rm", "web", "python3", "-c", cmd_str]

    result = subprocess.run(docker_cmd)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-deploy import smoke against the built web container."
    )
    parser.add_argument(
        "--project",
        default=None,
        help="Docker Compose project name (-p flag). Omit to use the default.",
    )
    args = parser.parse_args()
    sys.exit(run_smoke(compose_project=args.project))


if __name__ == "__main__":
    main()
