<!--
file: po-requests.md
purpose: Items the Product Owner needs from the human operator to unblock sprint-1 (P0)
owner: product-owner
last-updated: 2026-06-14
-->

# PO Requests — Human Operator Inputs (sprint-1 / P0)

These block or de-risk sprint-1. Items 1–2 are **hard blockers for US-6** (CD → GHCR → VPS).
Items 3–4 are heads-up / later-phase levers, surfaced now so nothing is a surprise.

## 1. GitHub repository + remote — BLOCKER for US-6
**Status:** `git remote -v` returns nothing — there is no GitHub remote configured on this repo,
so `gh issue create` and the CD pipeline have nowhere to push.
**Need from operator (or confirm an agent may do it):**
- Confirm the GitHub repo name/org for solanaTrilly (e.g. `<org>/solanatrilly`).
- Confirm whether agents may run `gh repo create` + `git remote add origin …` + initial push,
  or whether the operator will create the repo and grant push access.
**Until resolved:** GitHub Issues for US-1…US-7 cannot be created, and US-6 (CD) cannot be wired.

## 2. CD secrets (GHCR + VPS) — BLOCKER for US-6 deploy step
The CD pipeline (PRD §15.4) needs these as **GitHub Actions repository secrets** (never committed):
- A GHCR push credential (the built-in `GITHUB_TOKEN` with `packages: write` is usually enough —
  confirm GHCR is enabled for the repo).
- A VPS SSH deploy key (private key as a secret) authorized for `root@140.82.43.36`, used only by
  the deploy step to `docker compose -p solanatrilly … pull && up -d` on the box.
**Need from operator:** provision/authorize the VPS deploy key and confirm GHCR is enabled.

## 3. Firehose budget acknowledgement (US-7 — informational)
US-7 seeds `ops/firehose_activation_log.md` with the project-wide budget: **10 Birdeye + 10
Helius activations**, 0 used. Birdeye/Helius API keys are already in `.env`. No action needed now —
flagged so the operator knows the ledger starts here and every future activation is PR-reviewed.

## 4. Operator-only levers (NOT needed in sprint-1 — heads-up for later phases)
Per PRD §15.6, three actions remain operator-only and are **out of scope for P0**:
1. Provision the production trading-wallet secret (real-SOL key) — needed only at Cutover.
2. Start the firehose for production.
3. Enable real-capital trading.
Sprint-1 reaches a deployed, isolated, hello-world staging stack — none of these levers are
required to complete it. Listed so the roadmap dependency is visible.

---
*Resolve item 1 first — it unblocks both GitHub issue creation and the CD pipeline.*
