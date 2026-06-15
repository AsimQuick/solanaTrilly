# ---
# module: core.models
# sprint: sprint-4
# story: US-5 AC-5.2, US-9 AC-9.1, US-9 AC-9.2, US-9 AC-9.3, US-10 AC-10.4, US-14 AC-14.1, US-14 AC-14.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, core.encoders, simple_history, core.schemas, pydantic
# ---
# Domain models live here. Run `docker compose run --rm web python manage.py
# makemigrations` after adding models, and commit the generated migration.
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import models
from pydantic import ValidationError as PydanticValidationError
from simple_history.models import HistoricalRecords

from core.encoders import JsonSafeEncoder
from core.schemas import PipelineConfigSchema


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

    def clean(self):
        """Validate sections through the Pydantic schema on the write path (AC-10.4, PRD §5.2).

        Only fires when the invariant-bearing sections carry their minimum required
        fields, so that empty or partial draft rows remain storable.  A populated
        config that violates a §5.2 invariant is rejected with Django ValidationError
        so it CANNOT be persisted via the admin UI or direct .save() calls.
        """
        tape = self.tape or {}
        scoring = self.scoring or {}
        outcome = self.outcome or {}
        # Skip when the required cross-section fields are absent (incomplete config).
        if not (
            "idle_kill_ttl_s" in tape
            and "score_at_elapsed_s" in scoring
            and "window_s" in scoring
            and "window_s" in outcome
        ):
            return
        try:
            PipelineConfigSchema.from_model_sections(
                detection=self.detection or {},
                tape=tape,
                scoring=scoring,
                outcome=outcome,
                trading=self.trading or {},
            )
        except PydanticValidationError as exc:
            raise DjangoValidationError(str(exc)) from exc

    def save(self, *args, **kwargs):
        """Run Pydantic schema validation before persisting (AC-10.4, PRD §5.2)."""
        self.clean()
        super().save(*args, **kwargs)

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


class Token(models.Model):
    """One row per graduated pump.fun token (PRD §8, US-14).

    Created by the detection consumer when a MEME_DATA event signals graduation.
    mint is the primary key (Solana mint address — globally unique).
    graduated_at / graduated_block_time together anchor t0 for the tape recorder.
    raw_graduation stores the verbatim event JSONB; encoder=JsonSafeEncoder keeps
    the H3/US-5 guard test green.
    """

    # --- status vocabulary (VARCHAR-width discipline, PRD §8) ---
    STATUS_DETECTED = "DETECTED"
    STATUS_RECORDING = "RECORDING"
    STATUS_SCORED = "SCORED"
    STATUS_TRADED = "TRADED"
    STATUS_SKIPPED = "SKIPPED"
    STATUS_CHOICES = [
        (STATUS_DETECTED, "Detected"),
        (STATUS_RECORDING, "Recording"),
        (STATUS_SCORED, "Scored"),
        (STATUS_TRADED, "Traded"),
        (STATUS_SKIPPED, "Skipped"),
    ]
    _STATUS_MAX_LENGTH = 20

    mint = models.CharField(max_length=64, primary_key=True)
    pool_address = models.CharField(max_length=64)
    graduated_at = models.DateTimeField()
    graduated_block_time = models.IntegerField()
    dex_source = models.CharField(max_length=64)
    raw_graduation = models.JSONField(encoder=JsonSafeEncoder)
    status = models.CharField(
        max_length=_STATUS_MAX_LENGTH,
        choices=STATUS_CHOICES,
        default=STATUS_DETECTED,
    )

    class Meta:
        app_label = "core"
        db_table = "tokens"
