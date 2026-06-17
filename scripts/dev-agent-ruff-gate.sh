#!/usr/bin/env bash
# ---
# file: scripts/dev-agent-ruff-gate.sh
# project: solanatrilly
# purpose: AI dev-agent ruff gate — PreToolUse hook that intercepts git commit Bash
#          calls and runs ruff check BEFORE the commit lands, catching I001/E501/F401
#          violations before they reach the CI Lint step (US-47 AC-47.1)
# story: US-47 AC-47.1
# sprint: sprint-10
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: ruff (via Docker or host), scripts/pre-commit.sh
# ---
#
# Wired as a Claude Code PreToolUse hook for the Bash tool in
# .claude/settings.local.json.  Claude Code runs this script before EVERY Bash
# tool call; the script exits 0 immediately for non-commit commands so overhead
# is negligible.
#
# Exit codes (Claude Code PreToolUse semantics):
#   0   — allow the tool call to proceed
#   2   — block the tool call (violation found); Claude Code surfaces the output
#
# Stdin: JSON object from Claude Code with at minimum {"command": "..."}
#        (Claude Code PreToolUse hook contract).

# NOTE: deliberately NOT using `set -e`. Under Claude Code PreToolUse semantics
# only exit code 2 BLOCKS the tool call; any other non-zero code is treated as a
# non-blocking error and the commit would still proceed. `set -e` would make a
# ruff failure exit with ruff's own code (1) → non-blocking → the violation would
# slip through. We therefore capture ruff's exit code explicitly and exit 2.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Read the Bash command from the PreToolUse JSON payload on stdin ──────────
INPUT="$(cat)"

# Only gate on git commit commands; all other Bash calls pass through.
if ! echo "${INPUT}" | grep -qE '"git commit|git commit'; then
    exit 0
fi

# ── Run ruff check — mirrors scripts/pre-commit.sh strategy ─────────────────
cd "${REPO_ROOT}"

echo "[dev-agent-ruff-gate] Running ruff check before git commit..." >&2

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
    docker compose run --rm web ruff check .
    RUFF_EXIT=$?
elif command -v ruff &>/dev/null; then
    ruff check .
    RUFF_EXIT=$?
else
    echo "ERROR: ruff not found on PATH and Docker is not available." >&2
    echo "Ensure Docker is running or ruff is installed on the host." >&2
    exit 2
fi

if [ "${RUFF_EXIT}" -ne 0 ]; then
    echo "[dev-agent-ruff-gate] Ruff check FAILED — fix violations before committing." >&2
    exit 2
fi

echo "[dev-agent-ruff-gate] Ruff check passed — proceeding with commit." >&2
exit 0
