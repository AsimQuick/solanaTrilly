# ---
# module: core.tasks
# sprint: sprint-7, sprint-8
# story: US-1 AC-1.5, US-16 AC-16.2, US-31 AC-31.1, AC-31.2, AC-31.3; US-38 AC-38.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: celery, core.detection.birdeye_sweep, core.feature_builder, core.models
# ---
"""Core Celery tasks — autodiscovered by the Celery worker on startup."""
from celery import shared_task


@shared_task(name="core.tasks.add")
def add(x, y):
    """Return the sum of x and y. Primary sample task for AC-1.5 verification."""
    return x + y


@shared_task(name="core.tasks.ping")
def ping():
    """Health-check sample task — returns 'pong'."""
    return "pong"


@shared_task(name="core.tasks.birdeye_graduation_sweep")
def birdeye_graduation_sweep():
    """Third-belt periodic sweep: reconcile missed graduations via Birdeye REST (AC-16.2).

    Celery-beat runs this every 5 minutes (CELERY_BEAT_SCHEDULE in config/settings.py).
    Fetches recently graduated tokens from the Birdeye REST API and reconciles any
    absent from the local tokens table.  Idempotent: duplicate events for the same
    mint produce no additional rows (get_or_create semantics).

    Returns the count of newly-created Token rows.
    """
    from core.detection.birdeye_sweep import (
        fetch_birdeye_recent_graduations,
        reconcile_graduation_events,
    )

    events = fetch_birdeye_recent_graduations()
    return reconcile_graduation_events(events)


@shared_task(name="core.tasks.build_features")
def build_features(
    feature_set_id,
    mint_cohort,
    label_def,
    lake_base_dir=None,
    output_path=None,
    *,
    window_s: int = 120,
):
    """Feature Builder: extract features for a mint cohort from the lake (AC-31.1, PRD §6.5).

    Runs the SHARED vendored extractor (US-30 FeatureExtractor) over the lake for each
    mint in the cohort, producing a CSV export.  This task MUST run on the dedicated
    celery-worker container — never on web/gunicorn (#289 lesson).

    Label leak-free constraint (AC-31.3): if label_def contains ``label_start_s`` it must
    be >= window_s; otherwise a ``ValueError`` is raised BEFORE any DB lookup.

    Args:
        feature_set_id: PK of the FeatureSet to use.
        mint_cohort: List of mint address strings.
        label_def: Label definition dict (for AC-31.2/31.3; passed through here).
        lake_base_dir: Root of the lake tree (defaults to 'lake/tapes').
        output_path: Destination CSV path (defaults to /tmp/features_<id>.csv).
        window_s: Feature extraction window in seconds (default 120); used for leak-free
            label validation.

    Returns:
        {"path": str, "row_count": int}
    """
    from core.feature_builder import build_features_core, validate_label_def

    # AC-31.3: validate BEFORE any DB lookup so the rejection fires at submission time.
    validate_label_def(label_def, window_s)

    from core.models import FeatureSet

    if lake_base_dir is None:
        lake_base_dir = "lake/tapes"
    if output_path is None:
        output_path = f"/tmp/features_{feature_set_id}.csv"

    fs = FeatureSet.objects.get(pk=feature_set_id)
    return build_features_core(
        feature_set=fs,
        mint_cohort=mint_cohort,
        label_def=label_def,
        lake_base_dir=lake_base_dir,
        output_path=output_path,
        window_s=window_s,
    )


@shared_task(name="core.tasks.ship_lake_partitions")
def ship_lake_partitions(
    reference_date_str=None,
    src_base=None,
    dst_base=None,
    window_days=None,
):
    """Ship the day's lake partitions from VPS to local lake (AC-38.1, oracle §5).

    ONE-WAY: VPS = capture+serve; local = single source of truth for history.
    Runs on the dedicated celery-worker/celery-beat container — NEVER web/gunicorn
    (#289 lesson).  The ship window (how many trailing days to ship) is read from
    ``get_active_config().tape.lake_ship_window_days`` (config-driven, Principle #1).

    Args:
        reference_date_str: UTC date string ``"YYYY-MM-DD"`` representing 'today'.
            When ``None`` the current UTC date is read from the injected Clock
            seam (``WallClock``, AC-2.2) — never datetime.now() directly.  For
            testing, pass any date string to keep the task deterministic.
        src_base: Source lake root path (VPS lake).  Defaults to ``"lake/tapes"``.
        dst_base: Destination lake root path (local lake).  Defaults to
            ``"lake/local"``.
        window_days: Override for the config-driven ship window (integer ≥ 1).
            Used by tests to bypass the DB; production omits this and reads from
            ``get_active_config()``.

    Returns:
        List of dicts ``{"date_str", "row_count", "manifest"}`` for each
        shipped partition; empty list if no partitions were found.
    """
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from core.clock import WallClock
    from core.resolver import get_active_config
    from core.tape.lake_ship import ship_lake

    # --- resolve window_days (config-driven, Principle #1) ---
    if window_days is None:
        config = get_active_config()
        window_days = config.tape.lake_ship_window_days if config else 1

    # --- resolve reference date via the Clock seam (AC-2.2), never datetime.now() ---
    if reference_date_str is None:
        reference_date_str = WallClock().now().strftime("%Y-%m-%d")

    # --- resolve paths (literal defaults; beat schedule may override via kwargs) ---
    if src_base is None:
        src_base = "lake/tapes"
    if dst_base is None:
        dst_base = "lake/local"

    # Build the list of dates to ship: trailing window_days days before reference
    ref = datetime.strptime(reference_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    date_strs = [
        (ref - timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(1, window_days + 1)
    ]

    results = ship_lake(Path(src_base), Path(dst_base), date_strs)
    return [
        {
            "date_str": r.date_str,
            "row_count": r.row_count,
            "manifest": str(r.manifest_written),
        }
        for r in results
    ]
