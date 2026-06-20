# ---
# module: core.models
# sprint: sprint-7, sprint-9, sprint-10
# story: US-5 AC-5.2, US-9 AC-9.1, US-9 AC-9.2, US-9 AC-9.3, US-10 AC-10.4,
#        US-14 AC-14.1, US-14 AC-14.2, US-17 AC-17.1, US-17 AC-17.2,
#        US-23 AC-23.1, US-29 AC-29.1, US-29 AC-29.2, US-29 AC-29.3,
#        US-42 AC-42.1, US-43 AC-43.1, US-51 AC-51.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: django, core.encoders, simple_history, core.schemas, pydantic
# ---
# Domain models live here. Run `docker compose run --rm web python manage.py
# makemigrations` after adding models, and commit the generated migration.
import hashlib
import json

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


class Swap(models.Model):
    """One row per recorded PumpSwap swap (PRD §8, US-17).

    mint is indexed against the 'tokens' table (FK-or-index per AC-17.1).
    owner is the tx signer (nullable — None excluded from aggregate counts).
    rel = block_time − token.graduated_block_time (anchored to DB Token row).
    """

    SIDE_BUY = "buy"
    SIDE_SELL = "sell"
    SIDE_CHOICES = [
        (SIDE_BUY, "Buy"),
        (SIDE_SELL, "Sell"),
    ]
    _SIDE_MAX_LENGTH = 4

    mint = models.CharField(max_length=64, db_index=True)
    block_time = models.IntegerField()
    slot = models.IntegerField()
    signature = models.CharField(max_length=128)
    side = models.CharField(max_length=_SIDE_MAX_LENGTH, choices=SIDE_CHOICES)
    price = models.FloatField()
    vol_sol = models.FloatField()
    vol_usd = models.FloatField()
    sol_usd = models.FloatField()
    owner = models.CharField(max_length=64, null=True, blank=True)
    base_reserve = models.BigIntegerField(null=True, blank=True)
    quote_reserve = models.BigIntegerField(null=True, blank=True)
    rel = models.FloatField()

    class Meta:
        app_label = "core"
        db_table = "swaps"
        indexes = [
            models.Index(
                fields=["mint", "block_time", "slot", "signature"],
                name="swap_mint_block_slot_sig_idx",
            ),
        ]


class Snapshot(models.Model):
    """One row per token's score-time read (PRD §8, US-23).

    Holds the verbatim holders/authority/liquidity payload captured at score
    time.  mint is indexed against the 'tokens' table (FK-or-index per
    AC-23.1); the at-most-one-row-per-token discipline (AC-23.2) is enforced
    via a unique constraint on mint.  raw uses JsonSafeEncoder so the H3/US-5
    guard stays green.
    """

    mint = models.CharField(max_length=64, db_index=True, unique=True)
    taken_at = models.DateTimeField()
    elapsed_s = models.IntegerField()
    raw = models.JSONField(encoder=JsonSafeEncoder)

    class Meta:
        app_label = "core"
        db_table = "snapshots"


class FeatureSet(models.Model):
    """Feature extraction contract (PRD §8, §6.4.5, US-29).

    A FeatureSet is a versioned, hashed extraction contract tying the offline lab to
    the live scorer.  It records the ordered feature column list, the live-servable
    subset (D2 — features computable at score time), the vendored math version, and a
    deterministic content hash so that same raw data + same FeatureSet → byte-identical
    output (Principle #2, §6.4.5).

    columns        — ordered list of all feature names produced by the extractor.
    live_servable  — subset of columns computable at live score time (D2); features
                     present in columns but absent here are training-only (D3).
    math_version   — version identifier of the vendored tape_microstructure module
                     (e.g. 'solanabilly3:sprint-7'); must match the vendored file.
    hash           — SHA-256 of canonical(columns, math_version); computed in AC-29.2.
    """

    version = models.CharField(max_length=64)
    math_version = models.CharField(max_length=64)
    columns = models.JSONField(default=list, encoder=JsonSafeEncoder)
    live_servable = models.JSONField(default=list, encoder=JsonSafeEncoder)
    hash = models.CharField(max_length=64, db_index=True)
    notes = models.TextField(blank=True, default="")

    @staticmethod
    def compute_hash(columns: list, math_version: str) -> str:
        """SHA-256 of canonical(columns, math_version) — pure function, §6.4.5.

        Column order is preserved in the JSON array, so reordering columns
        produces a different hash.  Bumping math_version also changes the hash.
        The hex digest is 64 characters, matching the hash field max_length.
        """
        canonical = json.dumps(
            {"columns": columns, "math_version": math_version},
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    class Meta:
        app_label = "core"
        db_table = "feature_sets"


class ModelRegistry(models.Model):
    """Promoted model artifact registry (PRD §7.4, US-42 AC-42.1).

    Records every promoted model artifact in an audited, immutable-row,
    at-most-one-active discipline that mirrors PipelineConfig/FeatureSet.

    MODEL-AGNOSTIC: the registry stores whatever model is promoted; v3.2 is the
    first, never hardcoded here.  For a BLEND artifact the required fields are:

    kind                    — artifact type, e.g. 'lightgbm_regression_blend'.
    feature_list            — bound ordered list of feature names (binding order
                              per PRD §7.4: model.feature_list == booster.feature_name()).
    labels_seeds_manifest   — 3 labels × 5 seeds structure describing the booster set.
    blend_transform_descriptor — rank-average blend recipe parameters.
    artifact_content_hashes — SHA-256 content hash(es) of the artifact files.
    model_version           — version string of the promoted model artifact.
    feature_set_version     — version of the FeatureSet contract it was trained against.
    is_active               — exactly one row may be True at a time (activate_model()).
    """

    kind = models.CharField(max_length=128)
    feature_list = models.JSONField(default=list, encoder=JsonSafeEncoder)
    labels_seeds_manifest = models.JSONField(default=dict, encoder=JsonSafeEncoder)
    blend_transform_descriptor = models.JSONField(default=dict, encoder=JsonSafeEncoder)
    artifact_content_hashes = models.JSONField(default=dict, encoder=JsonSafeEncoder)
    model_version = models.CharField(max_length=128)
    feature_set_version = models.CharField(max_length=128)
    # Absolute path to the artifact directory on disk (e.g. /path/to/trilly_pregrad_v3_2).
    # Written by promote_blend(); read by BlendScorer.from_registry() to locate boosters.
    artifact_dir = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, default="")

    history = HistoricalRecords()

    class Meta:
        app_label = "core"
        db_table = "model_registry"


class Annotation(models.Model):
    """Human annotation on a token (PRD §8, §13.3, US-51 AC-51.1).

    A SEPARATE store keyed on mint — NEVER writes back into the raw lake (§6.4.1).
    Multiple annotations per mint are retained (append-only by design).
    tags uses JsonSafeEncoder for JSONB-safe storage.
    """

    mint = models.CharField(max_length=64, db_index=True)
    author = models.CharField(max_length=128)
    tags = models.JSONField(default=list, encoder=JsonSafeEncoder)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core"
        db_table = "annotations"


class Prediction(models.Model):
    """Durable score-time record — the per-label breakdown the lab measures (US-78).

    Written by the live scoring path (run_firehose._score_tick) at the moment a
    graduated token is scored, BEFORE the gate decision, so the full breakdown is
    persisted whether or not the token is picked.  This is the source for the
    ``predictions_positions`` data-contract surface (directives §10) and the
    durable scoring store (AC-3-adjacent): ``Position`` persists only the single
    blend ``score``, which cannot reconstruct the per-label preds / percentiles the
    lab needs to compare the live soak against the offline scores.

    label_scores / label_ranks are JSON so the record is robust to a model shipping
    a different label set; the export projects named columns (ctrl/oracle/liq_pred).

    Idempotent: unique on (mint, score_time, model_id) — re-scoring the same token
    at the same anchor with the same model updates rather than duplicates.
    """

    mint = models.CharField(max_length=64, db_index=True)
    score_time = models.IntegerField(db_index=True)  # unix seconds (grad_block_time + score_at_elapsed_s)
    model_id = models.CharField(max_length=128, default="")

    # Raw per-label seed-averaged booster predictions ({"ctrl":.., "oracle":.., "liq":..}).
    label_scores = models.JSONField(default=dict, encoder=JsonSafeEncoder)
    # Per-label percentile ranks vs the frozen reference distribution.
    label_ranks = models.JSONField(default=dict, encoder=JsonSafeEncoder)
    # Blend = mean of the per-label ranks (the gate-relevant cross-sectional score).
    blend = models.FloatField()

    per_day_target = models.IntegerField()
    rank_cut = models.FloatField(null=True, blank=True)
    picked = models.BooleanField(default=False)  # gate decision (blend >= rank_cut)
    # The single graduation-time SOL/USD spot used to serve features in USD
    # (US-76 BREAK-1) — also the USD basis for the linked position's size/pnl.
    sol_usd_spot = models.FloatField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "core"
        db_table = "predictions"
        constraints = [
            models.UniqueConstraint(
                fields=["mint", "score_time", "model_id"],
                name="uniq_prediction_mint_scoretime_model",
            ),
        ]
        indexes = [
            models.Index(fields=["mint", "score_time"]),
        ]

    def __str__(self):
        return f"Prediction({self.mint[:8]}… t={self.score_time} blend={self.blend:.4f} picked={self.picked})"
