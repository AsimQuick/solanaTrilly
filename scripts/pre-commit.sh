#!/usr/bin/env bash
# ---
# file: scripts/pre-commit.sh
# project: solanatrilly
# purpose: Git pre-commit hook — ruff lint gate (I001/E501/F401) before push
# story: US-33 AC-33.2
# install: run 'make install-hooks' or 'bash scripts/install-hooks.sh'
# ---
#
# Runs 'ruff check .' inside Docker if available, else directly on host ruff.
# Fails the commit on any lint violation.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${REPO_ROOT}"

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    docker compose run --rm web ruff check .
elif command -v ruff &>/dev/null; then
    ruff check .
else
    echo "ERROR: ruff not found on PATH and Docker is not available." >&2
    echo "Run 'make install-hooks' from a Docker-ready terminal, or install ruff." >&2
    exit 1
fi
