# ---
# module: core.admin
# sprint: sprint-3, sprint-4
# story: US-9 AC-9.4, US-14 AC-14.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django, core.models, simple_history
# ---
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from core.models import PipelineConfig, Token


@admin.register(PipelineConfig)
class PipelineConfigAdmin(SimpleHistoryAdmin):
    """Admin for PipelineConfig — list + detail with history/diff (§5 operator surface)."""

    list_display = ["id", "version", "label", "is_active", "created_at", "created_by"]
    list_filter = ["is_active"]
    readonly_fields = ["created_at"]
    ordering = ["-created_at"]


@admin.register(Token)
class TokenAdmin(admin.ModelAdmin):
    """Admin for Token — graduated pump.fun token changelist + detail (US-14 AC-14.3)."""

    list_display = ["mint", "pool_address", "status", "dex_source", "graduated_at"]
    list_filter = ["status", "dex_source"]
    readonly_fields = ["mint", "graduated_at", "graduated_block_time", "raw_graduation"]
    ordering = ["-graduated_at"]
