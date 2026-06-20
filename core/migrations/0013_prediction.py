# ---
# migration: core.0013_prediction
# sprint: sprint-14
# story: US-78 (predictions_positions surface — durable score-time record)
# created-by: dev-team
# ---
import core.encoders
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_annotation'),
    ]

    operations = [
        migrations.CreateModel(
            name='Prediction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('mint', models.CharField(db_index=True, max_length=64)),
                ('score_time', models.IntegerField(db_index=True)),
                ('model_id', models.CharField(default='', max_length=128)),
                ('label_scores', models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder)),
                ('label_ranks', models.JSONField(default=dict, encoder=core.encoders.JsonSafeEncoder)),
                ('blend', models.FloatField()),
                ('per_day_target', models.IntegerField()),
                ('rank_cut', models.FloatField(blank=True, null=True)),
                ('picked', models.BooleanField(default=False)),
                ('sol_usd_spot', models.FloatField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'db_table': 'predictions',
                'indexes': [models.Index(fields=['mint', 'score_time'], name='predictions_mint_45f9ee_idx')],
                'constraints': [models.UniqueConstraint(fields=('mint', 'score_time', 'model_id'), name='uniq_prediction_mint_scoretime_model')],
            },
        ),
    ]
