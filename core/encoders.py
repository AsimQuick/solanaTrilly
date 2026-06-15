# ---
# module: core.encoders
# sprint: sprint-2
# story: US-5 AC-5.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, json, math, decimal
# ---
"""Single shared JSON encoder for all JSONB write sites (PRD §12 S7/H3).

JsonSafeEncoder is the canonical encoder class for every Django JSONField in
this project.  It guarantees psycopg-safe, spec-valid JSON output by:

  - Non-finite floats (NaN, Infinity, -Infinity) → JSON null
  - Decimal                                       → float
  - datetime / date / time                        → ISO-8601 string
    (inherited from DjangoJSONEncoder unchanged)
  - All other DjangoJSONEncoder type conversions  → inherited unchanged

Usage in a model field:
    from core.encoders import JsonSafeEncoder

    payload = models.JSONField(encoder=JsonSafeEncoder)
"""
import math
from decimal import Decimal

from django.core.serializers.json import DjangoJSONEncoder


class JsonSafeEncoder(DjangoJSONEncoder):
    """psycopg-safe JSON encoder — the single shared implementation for all JSONField writes."""

    def default(self, obj):
        if isinstance(obj, Decimal):
            value = float(obj)
            return None if not math.isfinite(value) else value
        return super().default(obj)

    def encode(self, obj):
        return super().encode(self._sanitize(obj))

    def _sanitize(self, obj):
        """Replace non-finite floats with None before encoding."""
        if isinstance(obj, float) and not math.isfinite(obj):
            return None
        if isinstance(obj, dict):
            return {k: self._sanitize(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            sanitized = [self._sanitize(item) for item in obj]
            return type(obj)(sanitized) if isinstance(obj, tuple) else sanitized
        return obj
