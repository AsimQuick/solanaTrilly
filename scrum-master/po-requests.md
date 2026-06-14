<!--
file: po-requests.md
purpose: Items the Product Owner needs from the human operator to unblock sprint-1 (P0)
owner: product-owner
last-updated: 2026-06-14
status: ALL RESOLVED — sprint-1 (P0) is fully unblocked. See **resolved** notes per item.
-->

# PO Requests — Human Operator Inputs (sprint-1 / P0)

> ✅ **ALL ITEMS RESOLVED (2026-06-14).** The GitHub remote, default branch, and CD secrets are
> provisioned; nothing here blocks P0. Read the **resolved** note under each item. Proceed with US-1…US-7.

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

**resolved** — The repo is **`AsimQuick/solanaTrilly`** (private), now created and wired. Root cause of
the block: an **old, unrelated repo of the same name** (last pushed 2026-02-27) was squatting the name,
so gitops's auto-create silently collided with it. The operator deleted the stale repo, and it was then
recreated from this working tree. Current state, verified:
- `origin` → `https://github.com/AsimQuick/solanaTrilly.git`
- `main` pushed and set as the **default branch**; `feature/US-1-AC-1.1` already pushed alongside it.
- GitHub Issues + the CD pipeline can now push; agents may branch and PR against `main` normally.
- **Branch protection is NOT set** — GitHub Free does not allow it on **private** repos (solanaBilly has
  none either). CI-pass is therefore enforced by the **gitops orchestrator** (`require_ci_pass: true`),
  not by GitHub. Treat a red required check as a hard merge-block regardless (PRD: green CI is a gate).

## 2. CD secrets (GHCR + VPS) — BLOCKER for US-6 deploy step
The CD pipeline (PRD §15.4) needs these as **GitHub Actions repository secrets** (never committed):
- A GHCR push credential (the built-in `GITHUB_TOKEN` with `packages: write` is usually enough —
  confirm GHCR is enabled for the repo).
- A VPS SSH deploy key (private key as a secret) authorized for `root@140.82.43.36`, used only by
  the deploy step to `docker compose -p solanatrilly … pull && up -d` on the box.
**Need from operator:** provision/authorize the VPS deploy key and confirm GHCR is enabled.

**resolved** — All CD secrets are provisioned on `AsimQuick/solanaTrilly`:
- **`VPS_SSH_KEY`** — set to the VPS's existing, already-authorized deploy key (`/root/.ssh/github_actions`,
  ed25519); **login verified** against `root@140.82.43.36`. We **reused** the existing key rather than
  minting a new one because `/root/.ssh/authorized_keys` is **immutable-hardened on purpose** (`chattr +i`)
  — we did not alter the VPS security posture. *(If you ever want a dedicated per-repo key: briefly
  `chattr -i ~/.ssh/authorized_keys && printf '%s\n' "<newpub>" >> ~/.ssh/authorized_keys && chattr +i
  ~/.ssh/authorized_keys`, then swap the secret.)*
- **`VPS_HOST`** = `140.82.43.36`, **`VPS_USER`** = `root`.
- **GHCR — no secret needed.** The CD workflow pushes with the built-in `GITHUB_TOKEN` +
  `permissions: { packages: write }`; it auto-creates `ghcr.io/asimquick/solanatrilly` on first push and
  links it to the repo. (Personal account → works out of the box.)
- The deploy step can `ssh $VPS_USER@$VPS_HOST` with `VPS_SSH_KEY`. 🔒 **HARD RULE (PRD §15.3):** every
  docker command on the VPS must be scoped **`-p solanatrilly`** and must **never** touch solanaBilly's
  containers/volumes — **solanaBilly is LIVE on this box.** Use distinct ports/db/volume/network.

## 3. Firehose budget acknowledgement (US-7 — informational)
US-7 seeds `ops/firehose_activation_log.md` with the project-wide budget: **10 Birdeye + 10
Helius activations**, 0 used. Birdeye/Helius API keys are already in `.env`. No action needed now —
flagged so the operator knows the ledger starts here and every future activation is PR-reviewed.

**resolved** — Acknowledged. Birdeye + Helius API keys confirmed present in `.env` by the operator.
Seed `ops/firehose_activation_log.md` at **10 Birdeye + 10 Helius, 0 used**. Every activation must be
deliberate, time-boxed, PR-logged, and **must bank durable fixtures** into the lake/golden set so it
compounds into the replay corpus (PRD §15.7). No operator action needed.

## 4. Operator-only levers (NOT needed in sprint-1 — heads-up for later phases)
Per PRD §15.6, three actions remain operator-only and are **out of scope for P0**:
1. Provision the production trading-wallet secret (real-SOL key) — needed only at Cutover.
2. Start the firehose for production.
3. Enable real-capital trading.
Sprint-1 reaches a deployed, isolated, hello-world staging stack — none of these levers are
required to complete it. Listed so the roadmap dependency is visible.

**resolved** (heads-up only — still out of scope for P0). Update: the operator has **already added the
trading-wallet key to `.env`** (early — that's fine). It changes nothing about safety: **trading stays in
observe/paper with ZERO capital until the operator explicitly enables real-capital trading** (lever 3),
and the firehose is only ever started deliberately (operator for production; dev/test within the §15.7
budget). The wallet key in `.env` is **inert** until trading is enabled. Verified: `.env` is gitignored +
untracked, so no secret is ever committed. None of the three levers are needed to finish sprint-1.

---
*All four items resolved 2026-06-14 — the GitHub remote, default branch, and CD secrets are live.
Sprint-1 (P0) is unblocked: create the US-1…US-7 issues and proceed.*
