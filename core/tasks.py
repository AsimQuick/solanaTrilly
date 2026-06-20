# ---
# module: core.tasks
# sprint: sprint-7, sprint-8, sprint-9, sprint-10
# story: US-1 AC-1.5, US-16 AC-16.2, US-31 AC-31.1/31.2/31.3; US-38 AC-38.1/38.2/38.3; US-43 AC-43.3; US-51 AC-51.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: celery, core.detection.birdeye_sweep, core.feature_builder, core.models,
#               core.scorer, core.dashboard.annotation_export
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


@shared_task(name="core.tasks.sweep_vps_lake_partitions")
def sweep_vps_lake_partitions(
    reference_date_str=None,
    lake_base=None,
    retention_days=None,
    local_base=None,
):
    """Expire VPS lake partitions older than the configured retention window (AC-38.2/38.3).

    When *local_base* is ``None``, runs :func:`~core.tape.lake_ship.sweep_expired_partitions`
    (AC-38.2 behaviour: expire by age only).

    When *local_base* is provided, runs
    :func:`~core.tape.lake_ship.sweep_expired_partitions_safe` instead, which
    gates expiry on a verified successful ship: a partition is expired ONLY if
    its local copy exists at *local_base* AND its content hash matches the local
    MANIFEST (AC-38.3 / data-lake.md no-data-loss rule).

    SCOPED and SAFE:
    - Only operates within *lake_base* (passed explicitly — never implicit).
    - Only removes directories matching the ``dt=YYYY-MM-DD`` naming pattern.
    - Uses ``shutil.rmtree`` — NO docker commands, prune, or volume removal.

    Args:
        reference_date_str: UTC date string ``"YYYY-MM-DD"`` representing today.
            When ``None``, reads from the injected Clock seam (``WallClock``,
            AC-2.2) — never ``datetime.now()`` directly.
        lake_base: Root of the VPS lake tree to sweep. Defaults to
            ``"lake/tapes"``.
        retention_days: Override for the config-driven retention window (integer
            1–7).  When ``None``, reads from
            ``get_active_config().tape.lake_retention_days``; falls back to 7.
        local_base: Root of the local lake tree (ship destination).  When
            provided, activates the AC-38.3 expire-after-ship gate — unshipped
            or hash-mismatched partitions are NEVER expired.

    Returns:
        ``{"expired": [...], "retained": [...]}`` — sorted date string lists.
    """
    from pathlib import Path

    from core.clock import WallClock
    from core.resolver import get_active_config
    from core.tape.lake_ship import (
        sweep_expired_partitions,
        sweep_expired_partitions_safe,
    )

    if retention_days is None:
        config = get_active_config()
        retention_days = config.tape.lake_retention_days if config else 7

    if reference_date_str is None:
        reference_date_str = WallClock().now().strftime("%Y-%m-%d")

    if lake_base is None:
        lake_base = "lake/tapes"

    if local_base is not None:
        result = sweep_expired_partitions_safe(
            Path(lake_base), Path(local_base), reference_date_str, retention_days
        )
    else:
        result = sweep_expired_partitions(
            Path(lake_base), reference_date_str, retention_days
        )
    return {"expired": result.expired, "retained": result.retained}


@shared_task(name="core.tasks.score_token")
def score_token(mint, features, ref_dist_data=None):
    """Score a graduated token using the active BLEND model (AC-43.3, US-43).

    Runs in the celery-worker container — NEVER on web/gunicorn (#289).
    Gated by PipelineState.scoring_enabled (§5.3/§15.6 — explicit operator
    action). This task NEVER sets scoring_enabled or trading_enabled True.
    trading_enabled is kept separate and is not touched here.

    Features must be supplied by the caller, produced by the single shared
    US-30 FeatureExtractor (Principle #2 — no scorer-local feature assembly).

    Args:
        mint: Token mint address string.
        features: Feature dict produced by the shared FeatureExtractor.
        ref_dist_data: Optional serialized per-label score dict (from
            ReferenceDistribution.to_dict()).  When supplied, used directly
            as the reference distribution for percentile ranking.
            When None, ScoringConfig.reference_dist_path is read from the
            active config (Principle #1 — config-driven path).

    Returns:
        {"mint": mint, "score": {...}} on success, or
        {"skipped": True, "reason": str} when scoring is disabled or no
        active model is registered.
    """
    from core.models import PipelineState
    from core.resolver import get_active_config, get_active_model
    from core.scorer import BlendScorer, ReferenceDistribution

    state = PipelineState.objects.filter(pk=1).first()
    if state is None or not state.scoring_enabled:
        return {"skipped": True, "reason": "scoring_enabled is False"}

    model_entry = get_active_model()
    if model_entry is None:
        return {"skipped": True, "reason": "no active model in registry"}

    scorer = BlendScorer.from_registry(model_entry)

    if ref_dist_data is not None:
        ref_dist = ReferenceDistribution.from_dict(ref_dist_data)
        result = scorer.score_single(features, ref_dist)
    else:
        config = get_active_config()
        ref_dist_path = config.scoring.reference_dist_path if config else None
        if ref_dist_path:
            ref_dist = ReferenceDistribution.from_file(ref_dist_path)
            result = scorer.score_single(features, ref_dist)
        else:
            result = scorer.score_pool([features])[0]

    return {"mint": mint, "score": result}


@shared_task(name="core.tasks.export_annotations")
def export_annotations(mint_corpus=None, output_path=None, *, dataset_id="annotation_export"):
    """Export labeled annotation dataset (annotations joined to corpus on mint) (AC-51.2, PRD §13.3).

    Runs on the dedicated celery-worker container — NEVER on web/gunicorn (#289).
    Joins Annotation rows to the supplied mint corpus, producing a CSV labeled
    dataset + MANIFEST following the §6.5 Feature Builder / US-36 export pattern.

    scoring_enabled and trading_enabled are NEVER touched by this task.

    Args:
        mint_corpus: List of mint address strings to include.  When None, all
            distinctly annotated mints from the Annotation table are used.
        output_path: Destination CSV file path.  Defaults to
            /tmp/annotation_export.csv.
        dataset_id: Unique identifier placed in the MANIFEST dataset_id field.
            Defaults to 'annotation_export'.

    Returns:
        {"path": str, "manifest": dict, "manifest_path": str, "row_count": int}
    """
    from core.dashboard.annotation_export import build_annotation_export
    from core.models import Annotation

    if mint_corpus is None:
        mint_corpus = list(
            Annotation.objects.values_list("mint", flat=True).distinct().order_by()
        )

    if output_path is None:
        output_path = "/tmp/annotation_export.csv"

    return build_annotation_export(mint_corpus, output_path, dataset_id=dataset_id)


@shared_task(name="core.tasks.export_data_contract")
def export_data_contract(
    out_dir=None,
    *,
    surfaces=None,
    lake_base_dir=None,
    dataset_prefix="data_contract",
):
    """US-78 data-contract export — write the lab's Parquet surfaces (§10).

    Builds the agreed Parquet surfaces consumed by the solanatrills lab, partitioned
    by UTC date and queryable by date-range + by-mint + by-wallet(signer).  Runs on
    the dedicated celery-worker container — NEVER on web/gunicorn (#289 lesson).

    This task only PROJECTS already-persisted state (the raw lake + Token registry)
    into the contract schema — it performs no scoring, no feature math, and NEVER
    touches scoring_enabled / trading_enabled.

    Args:
        out_dir: Export root.  Surfaces are written under {out_dir}/{surface}/.
            Defaults to /tmp/data_contract.
        surfaces: Iterable of surface names to build.  Defaults to
            ("swaps", "tokens").  (predictions_positions is delivered separately.)
        lake_base_dir: Root of the raw lake tree (defaults to 'lake/tapes').
        dataset_prefix: Prefix for each surface's manifest dataset_id.

    Returns:
        {"out_dir": str, "surfaces": {surface: <result dict>}}
    """
    from core.data_contract import build_swaps_dataset, build_tokens_dataset
    from core.models import Token
    from core.tape.lake_reader import LakeReader

    if out_dir is None:
        out_dir = "/tmp/data_contract"
    if lake_base_dir is None:
        lake_base_dir = "lake/tapes"
    if surfaces is None:
        surfaces = ("swaps", "tokens")
    surfaces = tuple(surfaces)

    # Build the {mint: base_decimals} map once from the Token registry — used to
    # derive base_amount_raw on the swaps surface.  Defensive dig: decimals live in
    # raw_graduation (top-level) or raw_graduation.raw.
    mint_decimals = {}
    token_dicts = []
    for tok in Token.objects.all().values(
        "mint", "graduated_block_time", "dex_source", "raw_graduation"
    ):
        token_dicts.append(tok)
        rg = tok.get("raw_graduation") or {}
        dec = rg.get("decimals")
        if dec is None:
            dec = (rg.get("raw") or {}).get("decimals")
        if dec is not None:
            try:
                mint_decimals[tok["mint"]] = int(dec)
            except (TypeError, ValueError):
                pass

    results = {}
    if "swaps" in surfaces:
        reader = LakeReader(base_dir=lake_base_dir)
        results["swaps"] = build_swaps_dataset(
            reader.iter_rows(),
            out_dir=out_dir,
            mint_decimals=mint_decimals,
            dataset_id=f"{dataset_prefix}_swaps",
        )
    if "tokens" in surfaces:
        results["tokens"] = build_tokens_dataset(
            token_dicts,
            out_dir=out_dir,
            dataset_id=f"{dataset_prefix}_tokens",
        )

    return {"out_dir": out_dir, "surfaces": results}
