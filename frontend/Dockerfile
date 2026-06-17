# ---
# file: frontend/Dockerfile
# stack: react+vite
# purpose: Multi-stage Dockerfile for the solanatrilly React+Vite dashboard.
#   Stage 'development': Vite dev server with hot reload (local docker-compose.yml).
#   Stage 'builder': produces /app/dist (CI / staging image build).
#   Stage 'production': nginx serves the built static assets (staging GHCR image).
#   Docker Rules: Node/Vite run INSIDE this container — NEVER installed on the host.
# created-by: dev-team
# sprint: sprint-10
# story: US-48 AC-48.1
# last-updated: 2026-06-17
# ---

# ── development ─────────────────────────────────────────────────────────────
FROM node:22-alpine AS development

WORKDIR /app

# Install deps first (cache-friendly layer)
COPY package*.json ./
RUN npm ci

# Source is volume-mounted at runtime (hot reload); copy here only for build parity
COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]

# ── builder ──────────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY . .

RUN npm run build

# ── production ───────────────────────────────────────────────────────────────
FROM nginx:1.27-alpine AS production

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy built React assets from builder stage
COPY --from=builder /app/dist /usr/share/nginx/html

# Minimal nginx config: SPA fallback so React Router works
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
