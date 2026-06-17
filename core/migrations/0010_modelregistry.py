# ---
# module: core.migrations.0010_modelregistry
# sprint: sprint-9
# story: US-42 AC-42.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.models, simple_history
# ---
import django.db.models.deletion
import simple_history.models
from django.conf import settings
from django.db import migrations, models

import core.encoders


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0009_featureset"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ModelRegistry",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("kind", models.CharField(max_length=128)),
                (
                    "feature_list",
                    models.JSONField(default=list, encoder=core.encoders.JsonSafeEncoder),
                ),
                (
                    "labels_seeds_manifest",
                    models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder),
                ),
                (
                    "blend_transform_descriptor",
                    models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder),
                ),
                (
                    "artifact_content_hashes",
                    models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder),
                ),
                ("model_version", models.CharField(max_length=128)),
                ("feature_set_version", models.CharField(max_length=128)),
                ("is_active", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("notes", models.TextField(blank=True, default="")),
            ],
            options={
                "db_table": "model_registry",
            },
        ),
        migrations.CreateModel(
            name="HistoricalModelRegistry",
            fields=[
                (
                    "id",
                    models.BigIntegerField(
                        auto_created=True,
                        blank=True,
                        db_index=True,
                        verbose_name="ID",
                    ),
                ),
                ("kind", models.CharField(max_length=128)),
                (
                    "feature_list",
                    models.JSONField(default=list, encoder=core.encoders.JsonSafeEncoder),
                ),
                (
                    "labels_seeds_manifest",
                    models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder),
                ),
                (
                    "blend_transform_descriptor",
                    models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder),
                ),
                (
                    "artifact_content_hashes",
                    models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder),
                ),
                ("model_version", models.CharField(max_length=128)),
                ("feature_set_version", models.CharField(max_length=128)),
                ("is_active", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(blank=True, editable=False)),
                ("notes", models.TextField(blank=True, default="")),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.CharField(max_length=100, null=True)),
                (
                    "history_type",
                    models.CharField(
                        choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")],
                        max_length=1,
                    ),
                ),
                (
                    "history_user",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "historical model registry",
                "verbose_name_plural": "historical model registries",
                "ordering": ("-history_date", "-history_id"),
                "get_latest_by": ("history_date", "history_id"),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
