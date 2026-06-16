#!/usr/bin/env bash
# ---
# file: scripts/install-hooks.sh
# project: solanatrilly
# purpose: Installs scripts/pre-commit.sh as .git/hooks/pre-commit
# story: US-33 AC-33.2
# usage: bash scripts/install-hooks.sh   (or: make install-hooks)
# ---

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET="${REPO_ROOT}/.git/hooks/pre-commit"

cp "${SCRIPT_DIR}/pre-commit.sh" "${TARGET}"
chmod +x "${TARGET}"
echo "Installed: ${TARGET}"
