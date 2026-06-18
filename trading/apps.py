# ---
# module: trading.apps
# sprint: sprint-13
# story: US-64 AC-64.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django
# ---
"""Django AppConfig for the trading namespace (shared execution apparatus).

Both pipelines (prediction model + copy-trade) call into this app.
No ready() method — no auto-start (US-11 / AC-64.1 AST guard).
"""

from django.apps import AppConfig


class TradingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "trading"
    label = "trading"
    verbose_name = "Trading"
