# ---
# module: core.admin
# sprint: sprint-3, sprint-4, sprint-5, sprint-6, sprint-7
# story: US-9 AC-9.4, US-14 AC-14.3, US-17 AC-17.4, US-23 AC-23.4, US-29 AC-29.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: django, core.models, simple_history
# ---
from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from core.models import FeatureSet, PipelineConfig, Snapshot, Swap, Token


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


@admin.register(Swap)
class SwapAdmin(admin.ModelAdmin):
    """Admin for Swap — immutable raw blockchain swap tape, read-only (US-17 AC-17.4)."""

    list_display = ["id", "mint", "side", "price", "block_time", "owner"]
    readonly_fields = [
        "mint",
        "block_time",
        "slot",
        "signature",
        "side",
        "price",
        "vol_sol",
        "vol_usd",
        "sol_usd",
        "owner",
        "base_reserve",
        "quote_reserve",
        "rel",
    ]
    ordering = ["-block_time", "-slot"]


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    """Admin for Snapshot — score-time read, immutable raw fields read-only (US-23 AC-23.4)."""

    list_display = ["mint", "taken_at", "elapsed_s"]
    readonly_fields = ["mint", "taken_at", "elapsed_s", "raw"]
    ordering = ["-taken_at"]


@admin.register(FeatureSet)
class FeatureSetAdmin(admin.ModelAdmin):
    """Read-only admin for FeatureSet — extraction contract viewer (US-29 AC-29.3).

    FeatureSets are immutable hashed configs; no row should ever be added or
    deleted through the admin.  All fields are readonly so the changelist +
    detail views are purely observational.
    """

    list_display = ["id", "version", "math_version", "hash"]
    readonly_fields = [
        "version",
        "math_version",
        "columns",
        "live_servable",
        "hash",
        "notes",
    ]
    ordering = ["id"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
