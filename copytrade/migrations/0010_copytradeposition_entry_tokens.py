"""Add entry_tokens to CopytradePosition (PR B2 curve honest-fill).

Token base units OUR simulated buy received at entry — needed to simulate the
size-impacted curve SELL at exit. Minimal on purpose: the pre-existing US-75
help_text drift on other fields is intentionally NOT bundled here.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('copytrade', '0009_copytradeposition_exit_void'),
    ]

    operations = [
        migrations.AddField(
            model_name='copytradeposition',
            name='entry_tokens',
            field=models.FloatField(blank=True, null=True),
        ),
    ]
