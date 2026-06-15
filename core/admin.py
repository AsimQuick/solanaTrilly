# ---
# module: core.admin
# sprint: sprint-3
# story: US-9 AC-9.4
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, core.models, simple_history
# ---
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from core.models import PipelineConfig


@admin.register(PipelineConfig)
class PipelineConfigAdmin(SimpleHistoryAdmin):
    """Admin for PipelineConfig — list + detail with history/diff (§5 operator surface)."""

    list_display = ["id", "version", "label", "is_active", "created_at", "created_by"]
    list_filter = ["is_active"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]
