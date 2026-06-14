# solanatrilly

## Product Vision
[Captured during standup — Project Lead will fill this in]

## Product Pillars
- [Pillar 1]
- [Pillar 2]
- [Pillar 3]

## Technology Stack
- Stack: django
- [Details captured during standup]

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

## Agent Reference
- **Product Owner:** Backlog, user stories, sprint files, change control (does NOT write code)
- **Dev Team:** Implements code in Docker, pushes to feature branches (does NOT modify PO/Tester sections)
- **Tester:** Quality gate — validates requirements, interprets CI results, enforces DoD (does NOT execute tests or modify source code)
- **Project Lead:** External Python script that orchestrates all agents — not an AI agent

## Current Sprint
See `/scrum-master/scrum-master.md` for current sprint status and controlled vocabulary.
See `/scrum-master/prd.md` for the full Product Requirements Document (if provided).
