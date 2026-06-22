# ---
# module: config.settings
# sprint: sprint-1, sprint-3
# story: US-1 AC-1.1, US-9 AC-9.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, django-environ, djangorestframework, channels, daphne, celery, simple_history
# ---
"""Django settings — 12-factor, environment-driven via django-environ."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env()

# SECURITY: override SECRET_KEY, DEBUG and ALLOWED_HOSTS via environment in prod.
SECRET_KEY = env("SECRET_KEY", default="dev-insecure-secret-key-change-me")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["*"])

# External data-source API keys (US-22): the listener/recorder reads these via
# Django settings (never os.environ directly — AC-11.1 guard).  Empty by default;
# set BIRDEYE_API_KEY / HELIUS_API_KEY in the environment to enable live firehose.
BIRDEYE_API_KEY = env("BIRDEYE_API_KEY", default="")
HELIUS_API_KEY = env("HELIUS_API_KEY", default="")

INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "channels",
    "rest_framework",
    "simple_history",
    "core",
    "copytrade",
    "trading",
]

# The MIDDLEWARE stack runs on EVERY request (top-down) and response (bottom-up).
# This is where cross-cutting concerns live: security headers, sessions, CSRF,
# auth, clickjacking protection. Add custom middleware here for rate limiting,
# request logging / correlation IDs, or multi-tenant resolution.
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Reads DATABASE_URL (e.g. postgres://user:pass@db:5432/name).
DATABASES = {
    "default": env.db("DATABASE_URL", default="postgres://postgres:postgres@db:5432/app"),
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django REST Framework — sensible defaults; tighten per project.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

ASGI_APPLICATION = "config.asgi.application"

# Channel layers — Redis backend
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env("REDIS_URL", default="redis://redis:6379/0")],
        },
    },
}

# Celery — Redis broker + result backend
CELERY_BROKER_URL = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_RESULT_BACKEND = env("REDIS_URL", default="redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

# Celery-beat periodic task schedule (AC-16.2 — third-belt graduation sweep;
#                                      AC-38.1 — daily VPS→local lake ship;
#                                      AC-38.2 — daily VPS retention sweep)
CELERY_BEAT_SCHEDULE = {
    "birdeye-graduation-sweep": {
        "task": "core.tasks.birdeye_graduation_sweep",
        "schedule": 300.0,  # every 5 minutes
    },
    "ship-lake-partitions": {
        "task": "core.tasks.ship_lake_partitions",
        "schedule": 86400.0,  # once per day (24 hours)
    },
    "sweep-vps-lake-partitions": {
        "task": "core.tasks.sweep_vps_lake_partitions",
        "schedule": 86400.0,  # once per day
    },
    # Curve-stage copy cohort: settle due observe positions with honest PnL (no-op
    # unless the active cohort is curvestage). EPIC-copy-curvestage-integration.
    "curvestage-settle": {
        "task": "copytrade.tasks.settle_curvestage_positions",
        "schedule": 60.0,  # every 60s
    },
    # Weekly retrain of the curvestage P(grad) classifier from the recorder lake
    # (no-op until the lake has >=7 days; keeps the lab seed until then).
    "curvestage-retrain": {
        "task": "copytrade.tasks.retrain_curvestage_classifier",
        "schedule": 604800.0,  # weekly (7 days)
    },
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Route the application loggers to stdout so containerized runs are observable
# via `docker logs` — without this, Python's default config swallows everything
# below WARNING and the live firehose daemon's [FIREHOSE] INFO observability
# (collection / graduation / score / paper-trade) is invisible. The level is
# env-configurable (DJANGO_LOG_LEVEL=DEBUG surfaces raw Birdeye/Helius frames
# for live format confirmation). stdout so structured events read cleanly in
# `docker logs`.
LOG_LEVEL = env("DJANGO_LOG_LEVEL", default="INFO")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
            "formatter": "standard",
        },
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "core": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "copytrade": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "trading": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
