# ---
# file: Dockerfile
# stack: django
# purpose: Production-ready Django container (slim — psycopg binary wheel means no build toolchain)
# created-by: project-lead
# ---

FROM python:3.12-slim

# Keep Python lean and unbuffered for container logs
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Python deps. psycopg[binary] ships a wheel, so no gcc/libpq-dev needed —
# smaller image and a much faster build on older hardware.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# collectstatic is run at deploy time (see deploy override), not at build —
# avoids needing prod env/secrets during image build.

EXPOSE 8000

# Production entrypoint. Local dev overrides this with runserver in docker-compose.yml.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2"]
