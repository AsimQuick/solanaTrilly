# ---
# module: core.resolver
# sprint: sprint-3
# story: US-11 AC-11.1, US-11 AC-11.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django.core.cache, django.db, core.models, core.schemas
# ---
"""Single cached resolver for the active PipelineConfig (PRD §5.3).

get_active_config() is the ONLY sanctioned read path for pipeline tunables.
Services must call this function; direct os.getenv or DB reads in service
modules are forbidden (enforced by the static-analysis guard in
core/tests/test_resolver_ac111.py).

activate_config(config_id) is the ONLY sanctioned write path for flipping the
active config (AC-11.2).  It is atomic: exactly one is_active=True row exists
at every point in time.
"""
from django.core.cache import cache
from django.db import transaction

from core.models import PipelineConfig
from core.schemas import PipelineConfigSchema

_CACHE_KEY = "active_pipeline_config_sections"
_CACHE_TTL = 300  # seconds; invalidated explicitly on activation (AC-11.2)


def get_active_config() -> PipelineConfigSchema | None:
    """Return the is_active PipelineConfig validated through PipelineConfigSchema.

    On cache hit: reconstructs the schema from cached section dicts — no DB query.
    On cache miss: queries DB, caches raw sections, returns the schema.
    Returns None when no active config row exists.

    The cache is invalidated by invalidate_active_config_cache(), which is called
    by the activation helper (AC-11.2) whenever the active config changes.
    """
    cached_sections = cache.get(_CACHE_KEY)
    if cached_sections is not None:
        return PipelineConfigSchema.from_model_sections(**cached_sections)

    try:
        config = PipelineConfig.objects.get(is_active=True)
    except PipelineConfig.DoesNotExist:
        return None

    sections = {
        "detection": config.detection or {},
        "tape": config.tape or {},
        "scoring": config.scoring or {},
        "outcome": config.outcome or {},
        "trading": config.trading or {},
    }
    cache.set(_CACHE_KEY, sections, _CACHE_TTL)
    return PipelineConfigSchema.from_model_sections(**sections)


def invalidate_active_config_cache() -> None:
    """Invalidate the cached active config (call after any activation change)."""
    cache.delete(_CACHE_KEY)


def activate_config(config_id: int) -> PipelineConfig:
    """Atomically flip is_active to the given config, deactivating all others.

    Guarantees exactly one is_active=True row at every point in time (AC-11.2):
      1. Within a single transaction, clear all is_active flags then set the
         target row's flag — both via .update() to skip clean() and history
         middleware complexity inside the transaction.
      2. After the transaction commits, invalidate the resolver cache so the
         next get_active_config() call reads the newly active row from the DB.

    Raises:
        PipelineConfig.DoesNotExist: if config_id does not refer to an
            existing row (checked after the updates).
    """
    with transaction.atomic():
        # Deactivate all currently active configs in one statement.
        PipelineConfig.objects.filter(is_active=True).update(is_active=False)
        # Activate the target config.
        PipelineConfig.objects.filter(pk=config_id).update(is_active=True)
        # Verify the target exists (raises DoesNotExist if it does not).
        config = PipelineConfig.objects.get(pk=config_id)

    # Invalidate AFTER the transaction commits so any concurrent reader that
    # hits the cache between the two .update() calls still gets a consistent
    # (old) value rather than an empty one.
    invalidate_active_config_cache()
    return config
