# ---
# module: core.migrations.0011_modelregistry_artifact_dir
# sprint: sprint-9
# story: US-43 AC-43.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: core.migrations.0010_modelregistry
# ---
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0010_modelregistry"),
    ]

    operations = [
        migrations.AddField(
            model_name="modelregistry",
            name="artifact_dir",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="historicalmodelregistry",
            name="artifact_dir",
            field=models.TextField(blank=True, default=""),
        ),
    ]
