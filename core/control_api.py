# ---
# module: core.control_api
# sprint: sprint-11
# story: US-56 AC-56.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: django, djangorestframework, core.models, core.resolver
# ---
"""Read-only DRF API surface for PipelineConfig (US-9) and ModelRegistry (US-42).

Provides view/diff/history endpoints over existing backends. No new config or
registry math — reads and diffs existing rows only (Principle #1/#2).
"""
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from core.models import ModelRegistry, PipelineConfig
from core.resolver import get_active_model

# ---------------------------------------------------------------------------
# Serializers (read-only)
# ---------------------------------------------------------------------------


class PipelineConfigSerializer(serializers.ModelSerializer):
    """Read-only serializer for PipelineConfig. Excludes created_by (FK)."""

    class Meta:
        model = PipelineConfig
        fields = [
            "id",
            "version",
            "label",
            "is_active",
            "created_at",
            "notes",
            "detection",
            "tape",
            "scoring",
            "outcome",
            "trading",
            "feature_set_id",
            "model_id",
        ]
        read_only_fields = fields


class ModelRegistrySerializer(serializers.ModelSerializer):
    """Read-only serializer for ModelRegistry."""

    class Meta:
        model = ModelRegistry
        fields = [
            "id",
            "kind",
            "feature_list",
            "labels_seeds_manifest",
            "blend_transform_descriptor",
            "artifact_content_hashes",
            "model_version",
            "feature_set_version",
            "artifact_dir",
            "is_active",
            "created_at",
            "notes",
        ]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------


def _serialize_config_history_record(record) -> dict:
    return {
        "history_id": record.history_id,
        "history_date": record.history_date.isoformat(),
        "history_type": record.history_type,
        "history_change_reason": record.history_change_reason,
        "id": record.id,
        "version": getattr(record, "version", None),
        "label": getattr(record, "label", None),
        "is_active": getattr(record, "is_active", None),
    }


def _serialize_model_history_record(record) -> dict:
    return {
        "history_id": record.history_id,
        "history_date": record.history_date.isoformat(),
        "history_type": record.history_type,
        "history_change_reason": record.history_change_reason,
        "id": record.id,
        "kind": getattr(record, "kind", None),
        "model_version": getattr(record, "model_version", None),
        "is_active": getattr(record, "is_active", None),
    }


# ---------------------------------------------------------------------------
# Diff functions (pure, deterministic)
# ---------------------------------------------------------------------------


def _flat_diff(old: dict, new: dict) -> dict:
    """Field-level diff of two flat dicts → {"added": {...}, "removed": {...}, "changed": {...}}."""
    old_keys = set(old.keys())
    new_keys = set(new.keys())
    return {
        "added": {k: new[k] for k in sorted(new_keys - old_keys)},
        "removed": {k: old[k] for k in sorted(old_keys - new_keys)},
        "changed": {
            k: {"from": old[k], "to": new[k]}
            for k in sorted(old_keys & new_keys)
            if old[k] != new[k]
        },
    }


def compute_config_diff(v1: PipelineConfig, v2: PipelineConfig) -> dict:
    """Section-level diff between two PipelineConfig instances. Pure/deterministic."""
    # Compare metadata scalar fields
    meta_diff = {}
    for field in ("version", "label", "notes", "feature_set_id", "model_id"):
        val1, val2 = getattr(v1, field), getattr(v2, field)
        if val1 != val2:
            meta_diff[field] = {"from": val1, "to": val2}

    # Compare each section
    sections = {}
    for section in ("detection", "tape", "scoring", "outcome", "trading"):
        s1 = getattr(v1, section) or {}
        s2 = getattr(v2, section) or {}
        diff = _flat_diff(s1, s2)
        if any(diff[k] for k in ("added", "removed", "changed")):
            sections[section] = diff

    return {"v1_id": v1.pk, "v2_id": v2.pk, "meta": meta_diff, "sections": sections}


def compute_model_diff(base: ModelRegistry, candidate: ModelRegistry) -> dict:
    """Field-level diff between two ModelRegistry instances. Pure/deterministic."""
    diff = {}
    for field in (
        "kind",
        "model_version",
        "feature_set_version",
        "feature_list",
        "labels_seeds_manifest",
        "blend_transform_descriptor",
        "artifact_content_hashes",
    ):
        val1, val2 = getattr(base, field), getattr(candidate, field)
        if val1 != val2:
            diff[field] = {"from": val1, "to": val2}
    return {"base_id": base.pk, "candidate_id": candidate.pk, "diff": diff}


# ---------------------------------------------------------------------------
# PipelineConfig views
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def config_control_view(request):
    """GET /api/control/config/ → {"active": {...}|null, "all": [...]}"""
    configs = PipelineConfig.objects.order_by("version")
    all_data = PipelineConfigSerializer(configs, many=True).data

    active_qs = PipelineConfig.objects.filter(is_active=True).order_by("version")
    active_obj = active_qs.first()
    active_data = PipelineConfigSerializer(active_obj).data if active_obj else None

    return Response({"active": active_data, "all": list(all_data)})


@api_view(["GET"])
@permission_classes([AllowAny])
def config_detail_view(request, pk):
    """GET /api/control/config/<int:pk>/ → single PipelineConfig detail"""
    try:
        config = PipelineConfig.objects.get(pk=pk)
    except PipelineConfig.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    return Response(PipelineConfigSerializer(config).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def config_history_view(request, pk):
    """GET /api/control/config/<int:pk>/history/ → django-simple-history audit trail list"""
    try:
        config = PipelineConfig.objects.get(pk=pk)
    except PipelineConfig.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    records = config.history.all().order_by("history_date")
    return Response([_serialize_config_history_record(r) for r in records])


@api_view(["GET"])
@permission_classes([AllowAny])
def config_diff_view(request):
    """GET /api/control/config/diff/?v1=<pk>&v2=<pk> → section-level diff"""
    v1_pk = request.query_params.get("v1")
    v2_pk = request.query_params.get("v2")

    if not v1_pk or not v2_pk:
        return Response({"detail": "Both v1 and v2 query params are required."}, status=400)

    try:
        v1_pk_int = int(v1_pk)
        v2_pk_int = int(v2_pk)
    except (ValueError, TypeError):
        return Response({"detail": "v1 and v2 must be integers."}, status=400)

    try:
        v1 = PipelineConfig.objects.get(pk=v1_pk_int)
    except PipelineConfig.DoesNotExist:
        return Response({"detail": f"PipelineConfig pk={v1_pk_int} not found."}, status=404)

    try:
        v2 = PipelineConfig.objects.get(pk=v2_pk_int)
    except PipelineConfig.DoesNotExist:
        return Response({"detail": f"PipelineConfig pk={v2_pk_int} not found."}, status=404)

    return Response(compute_config_diff(v1, v2))


# ---------------------------------------------------------------------------
# ModelRegistry views
# ---------------------------------------------------------------------------


@api_view(["GET"])
@permission_classes([AllowAny])
def registry_control_view(request):
    """GET /api/control/registry/ → {"active": {...}|null, "all": [...]}"""
    registries = ModelRegistry.objects.order_by("created_at")
    all_data = ModelRegistrySerializer(registries, many=True).data

    active_obj = get_active_model()
    active_data = ModelRegistrySerializer(active_obj).data if active_obj else None

    return Response({"active": active_data, "all": list(all_data)})


@api_view(["GET"])
@permission_classes([AllowAny])
def registry_detail_view(request, pk):
    """GET /api/control/registry/<int:pk>/ → single ModelRegistry detail"""
    try:
        registry = ModelRegistry.objects.get(pk=pk)
    except ModelRegistry.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    return Response(ModelRegistrySerializer(registry).data)


@api_view(["GET"])
@permission_classes([AllowAny])
def registry_history_view(request, pk):
    """GET /api/control/registry/<int:pk>/history/ → django-simple-history audit trail list"""
    try:
        registry = ModelRegistry.objects.get(pk=pk)
    except ModelRegistry.DoesNotExist:
        return Response({"detail": "Not found."}, status=404)
    records = registry.history.all().order_by("history_date")
    return Response([_serialize_model_history_record(r) for r in records])


@api_view(["GET"])
@permission_classes([AllowAny])
def registry_diff_view(request):
    """GET /api/control/registry/diff/?candidate=<pk> → diff vs active model"""
    candidate_pk = request.query_params.get("candidate")

    if not candidate_pk:
        return Response({"detail": "candidate query param is required."}, status=400)

    try:
        candidate_pk_int = int(candidate_pk)
    except (ValueError, TypeError):
        return Response({"detail": "candidate must be an integer."}, status=400)

    try:
        candidate = ModelRegistry.objects.get(pk=candidate_pk_int)
    except ModelRegistry.DoesNotExist:
        return Response({"detail": f"ModelRegistry pk={candidate_pk_int} not found."}, status=404)

    active = get_active_model()
    if active is None:
        return Response(
            {"base_id": None, "candidate_id": candidate.pk, "diff": {}, "note": "No active model"}
        )

    return Response(compute_model_diff(active, candidate))
