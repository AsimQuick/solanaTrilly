# ---
# module: core.resolver
# sprint: sprint-3
# story: US-11 AC-11.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-15
# dependencies: django.core.cache, core.models, core.schemas
# ---
"""Single cached resolver for the active PipelineConfig (PRD §5.3).

get_active_config() is the ONLY sanctioned read path for pipeline tunables.
Services must call this function; direct os.getenv or DB reads in service
modules are forbidden (enforced by the static-analysis guard in
core/tests/test_resolver_ac111.py).
"""
from django.core.cache import cache

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
