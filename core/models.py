# ---
# module: core.models
# sprint: sprint-3
# story: US-5 AC-5.2, US-9 AC-9.1, US-9 AC-9.2, US-9 AC-9.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, core.encoders, simple_history
# ---
# Domain models live here. Run `docker compose run --rm web python manage.py
# makemigrations` after adding models, and commit the generated migration.
from django.contrib.auth import get_user_model
from django.db import models
from simple_history.models import HistoricalRecords

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


class PipelineConfig(models.Model):
    """Immutable, versioned config snapshot (PRD §8, §5.1).

    Each row is a named, versioned point-in-time snapshot of every tunable
    section.  Exactly one row may have is_active=True at a time (enforced by
    the US-11 resolver).  Rows are never mutated after creation; all config
    changes produce a new version.

    feature_set_id / model_id are placeholder integer slots for the
    FeatureSet and ModelRegistry FK relationships that land in P5/P7.  They
    are declared as bare PositiveIntegerFields now and will be converted to
    proper ForeignKey fields when those tables exist.
    """

    version = models.PositiveIntegerField()
    label = models.CharField(max_length=255)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        get_user_model(),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="pipeline_configs",
    )
    notes = models.TextField(blank=True, default="")

    # Tunable sections — each stored as a JSONB object.
    # encoder=JsonSafeEncoder keeps the US-5 guard test green (H3).
    detection = models.JSONField(encoder=JsonSafeEncoder, default=dict)
    tape = models.JSONField(encoder=JsonSafeEncoder, default=dict)
    scoring = models.JSONField(encoder=JsonSafeEncoder, default=dict)
    outcome = models.JSONField(encoder=JsonSafeEncoder, default=dict)
    trading = models.JSONField(encoder=JsonSafeEncoder, default=dict)

    # FK placeholder slots — filled in P5 (FeatureSet) and P7 (ModelRegistry).
    feature_set_id = models.PositiveIntegerField(null=True, blank=True)
    model_id = models.PositiveIntegerField(null=True, blank=True)

    # Audit trail — every create/update records who and when (US-9 AC-9.2).
    history = HistoricalRecords()

    class Meta:
        app_label = "core"
        db_table = "pipeline_config"


class PipelineState(models.Model):
    """Singleton row (always id=1) holding live pipeline control flags (PRD §8, §5.3).

    All flags default to False — explicit start, no silent auto-recovery (§5.3).
    Use PipelineState.get() to retrieve (or create) the singleton.
    """

    firehose_active = models.BooleanField(default=False)
    scoring_enabled = models.BooleanField(default=False)
    trading_enabled = models.BooleanField(default=False)

    class Meta:
        app_label = "core"
        db_table = "pipeline_state"

    def save(self, *args, **kwargs):
        # Force singleton: every save targets pk=1, normalising any 'second instance'.
        self.pk = 1
        # Remove force_insert so Django can UPDATE if the row already exists
        # (objects.create() passes force_insert=True; without this the second
        # create() call would raise a UniqueViolation when pk=1 already exists).
        kwargs.pop("force_insert", None)
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        """Return the singleton row, creating it with safe defaults if absent."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj
