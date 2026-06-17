# ---
# file: Dockerfile
# stack: django+channels+celery
# purpose: Production-ready Django ASGI container (slim — psycopg binary wheel means no build toolchain)
# created-by: dev-team
# sprint: sprint-1, sprint-9
# story: US-1 AC-1.1, US-42 AC-42.2
# last-updated: 2026-06-17
# ---

FROM python:3.12-slim

# Keep Python lean and unbuffered for container logs
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# libgomp is required by the LightGBM wheel (OpenMP runtime).
# libgomp1 is in the debian slim repo and adds ~500 KB — acceptable.
# (Docker Rules: scorer/ML deps live in-container; never installed on host.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Python deps. psycopg[binary] ships a wheel, so no gcc/libpq-dev needed —
# smaller image and a much faster build on older hardware.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# collectstatic is run at deploy time (see deploy override), not at build —
# avoids needing prod env/secrets during image build.

EXPOSE 8000

# Production entrypoint — Daphne ASGI server (replaces gunicorn/WSGI).
# Local dev docker-compose.yml overrides this command.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
