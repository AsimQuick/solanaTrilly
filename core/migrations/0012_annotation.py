# ---
# module: core.migrations.0012_annotation
# sprint: sprint-10
# story: US-51 AC-51.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.migrations.0011_modelregistry_artifact_dir
# ---
from django.db import migrations, models
import core.encoders


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_modelregistry_artifact_dir"),
    ]

    operations = [
        migrations.CreateModel(
            name="Annotation",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("mint", models.CharField(db_index=True, max_length=64)),
                ("author", models.CharField(max_length=128)),
                (
                    "tags",
                    models.JSONField(
                        default=list, encoder=core.encoders.JsonSafeEncoder
                    ),
                ),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "annotations",
                "app_label": "core",
            },
        ),
    ]
