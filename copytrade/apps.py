# ---
# module: copytrade.apps
# sprint: sprint-12
# story: US-58 AC-58.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django
# ---
"""Django AppConfig for the copytrade §5-isolated namespace."""
from django.apps import AppConfig


class CopytradeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "copytrade"
    label = "copytrade"
    verbose_name = "Copy Trade"
