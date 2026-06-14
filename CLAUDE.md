# solanatrilly

## Product Vision
solanaTrilly detects pump.fun tokens at graduation (the moment they complete the bonding curve and migrate to the PumpSwap AMM), records their complete swap tape, scores them against a promoted model, and trades the winners — with a real dashboard the operator can actually see. It replaces solanaBilly as the one live pipeline going forward.

## Product Pillars
- **Graduated-token detection & tape recording** — capture every PumpSwap swap from t0; the Birdeye tape is the single source of truth for all signal, both live and offline
- **Parity by construction** — one data source (Birdeye) on both sides, one vendored feature library (`tape_microstructure.py`), byte-identical results from dev through prod; a model's live score must equal its offline score
- **Research-first dashboard** — live positions on candles, cohort pattern-mining wall, human annotation → labeled export; the UI is a first-class modeling instrument and replaces solanaBilly's static DataTables entirely

## Technology Stack
- **Backend:** Django 5 + DRF (API / control plane) + Django Channels (realtime WebSocket)
- **Frontend:** React + Vite + TradingView Lightweight Charts (MIT)
- **Workers:** Celery + Redis (task queue, rate-limit token bucket, Channels layer)
- **Database:** PostgreSQL 16 with JSONB for raw lake storage
- **Config validation:** Pydantic v2 (typed `PipelineConfig` schema)
- **Data sources:** Birdeye WebSocket + REST (primary — detection + swap tape); Helius (`migrate` reconciler, `funded-by`, G2 raw-truth verification)
- **AMM:** PumpSwap (`pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA`) — sole graduation destination since 2025-03
- **Infrastructure:** Docker Compose (ALL services containerized per Docker Rules)

## Docker Rules (ALL AGENTS MUST FOLLOW)
- ALL services (databases, caches, queues) run INSIDE Docker containers
- NEVER run `apt install postgresql`, `brew install redis`, or install any service on the host machine
- ALL services are defined in `docker-compose.yml`
- Connect to services via Docker network hostnames (`db`, `redis`, `web`) — NOT `localhost`
- To start services: `docker compose up -d`
- To run tests: `docker compose run --rm web pytest` (or stack equivalent)
- The ONLY things that run on the host: git, claude, gh CLI, and the Project Lead script
- If you need a new service, add it to `docker-compose.yml` — do not install it on the host

## Project Conventions
- Commit format: `[US-X] Description of change`
- Branch format: `feature/US-X-AC-Y`
- All code files must include structured front matter / metadata header comments
- Sprint documentation lives in `/scrum-master/`
- `project-state.json` is owned exclusively by the Project Lead — agents do not modify it
- After updating any documentation in `/scrum-master/`, use `mcp__devrag__reindex_document` to re-index

## VPS Deployment (agents own this end-to-end)
- **VPS:** `ssh root@140.82.43.36`; deploy to `/root/solanatrilly/` (home dir of root user)
- **solanaBilly is LIVE at `/root/solanaBilly` — NEVER TOUCH IT.** No reason to go near it.
- **solanaTrilly isolation:** compose project `-p solanatrilly`; web on port **8002** (solanaBilly owns 8001); distinct Postgres DB + volume; distinct Redis; distinct Docker network
- **Forbidden:** unscoped `docker down`, `up --force-recreate`, `prune`, volume removal — ALWAYS scope with `-p solanatrilly`
- **CD pipeline:** GitHub Actions → GHCR → VPS; repo's `docker-compose.staging.yml` is the only compose; VPS never hand-edited
- **DoD per phase:** merged + deployed to VPS staging stack + smoke-tested there. "Works locally" is NOT done.
- **VPS presence from P0:** hello-world Django deployed through the full CD pipeline on day one

## Local Repos (read for vendoring — do not modify)
- **solanaBilly** (Flask predecessor): `/Users/asim/NoIcloud/solanabilly`
- **solanabilly3** (offline training lab): `/Users/asim/NoIcloud/solanabilly3`
  - Feature math to vendor: `solanabilly3/src/tape_microstructure.py` (copy verbatim; only allowed edit: `rels.ptp()` → `np.ptp(rels)` for numpy 2.x)
  - G1 golden fixture: `solanabilly3/data/microstructure/golden_fixture_t0t2_15s.json` (15 tokens) + regenerator `solanabilly3/scripts/make_microstructure_golden.py`
- **solanatrills** (strategy/labels lab): `/Users/asim/NoIcloud/solanatrills` *(inferred path — verify)*
  - Scorer golden vectors: `solanatrills/<model_dir>/<artifact_dir>/golden_scores.parquet` (every promoted model ships its own)
  - Live tape lake: `solanatrills/lake/tapes/` (parquet files — curve regime; use for G1 + scorer parity tests)
  - Settler oracle to port: `solanatrills/.../tape_resettle.py`

## Key External Resources
- **PumpSwap IDL (ground truth for account order):** `pump-fun/pump-public-docs/idl/pump_amm.json` — fetch from GitHub; vendor a pinned copy into the repo. IDL > chainstacklabs scripts if they differ.
- **Port reference (buy/sell ix):** `chainstacklabs/pumpfun-bonkfun-bot` → `learning-examples/pumpswap/manual_buy_pumpswap.py` and `manual_sell_pumpswap.py`
- **Birdeye:** Professional package — generous allowance; spend deliberately, not needlessly. Watch credit burn.
- **Firehose budget:** 10 Birdeye + 10 Helius activations project-wide. Each must be logged in `ops/firehose_activation_log.md` and bank durable fixtures. G2 (graduated-token cross-source parity) needs one activation; all other golden vectors already exist.

## Agent Reference
- **Product Owner:** Backlog, user stories, sprint files, change control (does NOT write code)
- **Dev Team:** Implements code in Docker, pushes to feature branches (does NOT modify PO/Tester sections)
- **Tester:** Quality gate — validates requirements, interprets CI results, enforces DoD (does NOT execute tests or modify source code)
- **Project Lead:** External Python script that orchestrates all agents — not an AI agent

## Current Sprint
See `/scrum-master/scrum-master.md` for current sprint status and controlled vocabulary.
See `/scrum-master/prd.md` for the full Product Requirements Document (if provided).
