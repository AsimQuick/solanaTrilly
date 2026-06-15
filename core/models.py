# ---
# module: core.models
# sprint: sprint-2
# story: US-5 AC-5.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, core.encoders
# ---
# Domain models live here. Run `docker compose run --rm web python manage.py
# makemigrations` after adding models, and commit the generated migration.
from django.db import models

from core.encoders import JsonSafeEncoder


class RawEvent(models.Model):
    """Canonical JSONB write site — all raw inbound events land here.

    The payload field uses JsonSafeEncoder so every JSONB write is
    guaranteed to be psycopg-safe, spec-valid JSON (NaN/Inf → null,
    Decimal → float, datetime → ISO-8601).
    """

    payload = models.JSONField(encoder=JsonSafeEncoder)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core"
