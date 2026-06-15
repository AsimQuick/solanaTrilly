---
file: ops/firehose_activation_log.md
purpose: Project-wide live-firehose activation ledger — budget tracking for Birdeye and Helius WS activations (PRD §15.7)
sprint: sprint-2
story: US-7 AC-7.1 AC-7.2
status: seeded
created-by: dev-team
last-updated: 2026-06-15
---

# Firehose Activation Ledger

> **API keys are in `.env` — never committed to this ledger or any tracked file.**
> Keys are referenced by their env-var names (`BIRDEYE_API_KEY`, `HELIUS_API_KEY`) only.
> Confirmed present in `.env` by the operator (po-requests.md item 3). See PRD §15.7.

## Project-Wide Budget

| Source    | Total | Used | Remaining |
|-----------|------:|-----:|----------:|
| Birdeye   |    10 |    0 |        10 |
| Helius    |    10 |    0 |        10 |
| **Total** |**20** | **0**|    **20** |

Both Birdeye and Helius API keys are provisioned in `.env` (gitignored + untracked).
They are **never committed** — not here, not in any source file, not in any workflow file.
GitHub Actions uses `VPS_SSH_KEY`, `VPS_HOST`, and `VPS_USER` repo secrets for CD;
firehose keys remain in `.env` only.

## Per-Activation Protocol

Every activation is **deliberate, time-boxed (≤ 30 min default; adjustable by explicit PO
decision), and logged here as a PR-reviewed entry** so the remaining count is always visible
in git history.

### Activation table schema

| Column | Description |
|--------|-------------|
| `date` | ISO-8601 date of activation |
| `role/agent` | Who opened the firehose (dev-team / product-owner / tester) |
| `ws` | Which WebSocket: `Birdeye` or `Helius` |
| `purpose` | Why this activation was necessary (1-sentence) |
| `duration` | Actual elapsed time (≤ 30 min unless PO-approved extension) |
| `count_remaining` | Updated remaining budget after this row (Birdeye and Helius separately) |
| `fixtures_banked` | Path(s) of durable fixtures committed as part of this activation's PR |

## HARD RULE — Every Activation MUST Bank Durable Fixtures

> **Every activation MUST bank durable fixtures** (a tape sample, a detection sample, or golden
> vectors) into the lake / golden set. The spend compounds into the replay corpus (PRD §11) — after
> which all further dev replays offline and never needs to go live again for that scenario.
>
> An activation with no committed fixture is a budget burn with no return. It will be flagged by the
> Tester and blocked from merge until a fixture is added.

The firehose is the **source of fixtures, not the dev loop**. Spend a live window to *bank reality*,
then build against it forever.

## Activation Log

*(No activations yet — budget freshly seeded at project start.)*

| Date | Role/Agent | WS | Purpose | Duration | Count Remaining (Birdeye / Helius) | Fixtures Banked |
|------|-----------|-----|---------|----------|-------------------------------------|-----------------|
