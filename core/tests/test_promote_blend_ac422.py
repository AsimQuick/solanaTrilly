# ---
# module: core.tests.test_promote_blend_ac422
# sprint: sprint-9
# story: US-42 AC-42.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: tools.promote_model, core.models, core.pregrad_features, lightgbm, numpy, pytest
# ---
"""AC-42.2 — promote_model.py ACCEPTS A BLEND: 15 LightGBM boosters + rank-average
blend transform; feature-order gate enforced at write time; mismatch REJECTED.

The promoter loads boosters/<label>_s<seed>.txt (3 labels × 5 seeds = 15 files),
enforces model.feature_list == booster.feature_name() via the US-41 gate, and
records the artifact to ModelRegistry.  It REFUSES on:
  - wrong booster count (missing files)
  - feature-order mismatch in any booster

A "faithful fixture" of v3.2 is built in-test by training 15 tiny LightGBM
regression models over the exact 20 PRE_FEATURE_NAMES in binding order — real
.txt files that lgb.Booster() can load and that return the correct feature_name().

H1 ImportError trap — module-level imports of promote_blend and FeatureContractError
must succeed; deletion/rename fails pytest collection before any test runs.

Tests
-----
Wiring guard (H1 ImportError trap):
  test_promote_blend_importable
  test_feature_contract_error_importable

End-to-end success (DB-backed):
  test_promote_blend_creates_registry_entry
  test_promote_blend_records_correct_kind
  test_promote_blend_records_feature_list_in_binding_order
  test_promote_blend_records_labels_seeds_manifest
  test_promote_blend_records_blend_transform_descriptor
  test_promote_blend_records_content_hashes_for_all_boosters
  test_promote_blend_records_model_version
  test_promote_blend_records_feature_set_version
  test_promote_blend_entry_is_inactive_by_default
  test_promote_blend_idempotent_second_call_creates_second_row

Rejection — seeded mismatch (no DB needed):
  test_promote_blend_rejects_feature_order_mismatch
  test_promote_blend_rejects_wrong_booster_count_missing_file
  test_promote_blend_prints_remedy_on_feature_order_mismatch
  test_promote_blend_prints_remedy_on_booster_count_mismatch
"""
import json

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Feature names — the binding order for v3.2 (from core.pregrad_features)
# ---------------------------------------------------------------------------
from core.pregrad_features import PRE_FEATURE_NAMES

# ---------------------------------------------------------------------------
# H1 ImportError trap — failing to import either symbol means something was
# deleted or renamed; pytest collection itself fails before any test runs.
# ---------------------------------------------------------------------------
from tools.promote_model import FeatureContractError, promote_blend

_LABELS = ["ctrl", "oracle", "liq"]
_SEEDS = list(range(5))
_N_BOOSTERS = len(_LABELS) * len(_SEEDS)  # 15


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _train_tiny_booster(features: list[str], seed: int, path: str) -> None:
    """Train a minimal LightGBM regression model and save it to path."""
    import lightgbm as lgb

    rng = np.random.default_rng(seed)
    X = rng.standard_normal((30, len(features)))
    y = rng.standard_normal(30)

    train_data = lgb.Dataset(X, y, feature_name=features, free_raw_data=False)
    params = {
        "num_leaves": 4,
        "verbose": -1,
        "objective": "regression",
        "seed": seed,
    }
    bst = lgb.train(params, train_data, num_boost_round=2)
    bst.save_model(path)


def _write_meta(tmp_path, name: str = "trilly_pregrad_v3_2_fixture") -> None:
    """Write a minimal meta.json mirroring the v3.2 structure."""
    meta = {
        "name": name,
        "features": PRE_FEATURE_NAMES,
        "labels": {
            "ctrl": "clip(tr30_t1800_25,-100,1000)",
            "oracle": "clip(oracle_25,-100,1000)",
            "liq": "log1p(cumbv_peak)",
        },
        "selection_recipe": (
            "for each of 3 labels: average 5 seed boosters' predictions; "
            "percentile-rank that label-score; blend = mean of 3 percentile-ranks; "
            "pick top-K/day by blend"
        ),
        "feature_set": "enrich20_buyer_cohort",
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta))


@pytest.fixture(scope="module")
def v32_fixture_dir(tmp_path_factory):
    """Faithful v3.2 fixture: 15 tiny real LightGBM boosters + meta.json.

    scope=module so we train once and reuse across all tests in this file.
    The fixture creates the exact boosters/<label>_s<seed>.txt layout that
    promote_blend() expects.
    """
    tmp_path = tmp_path_factory.mktemp("v32_fixture")
    boosters_dir = tmp_path / "boosters"
    boosters_dir.mkdir()

    _write_meta(tmp_path)

    seed_counter = 0
    for label in _LABELS:
        for seed in _SEEDS:
            _train_tiny_booster(
                PRE_FEATURE_NAMES,
                seed=seed_counter,
                path=str(boosters_dir / f"{label}_s{seed}.txt"),
            )
            seed_counter += 1

    return tmp_path


@pytest.fixture
def misorder_fixture_dir(tmp_path):
    """Fixture where booster ctrl_s0.txt has its feature list reversed (order mismatch)."""
    boosters_dir = tmp_path / "boosters"
    boosters_dir.mkdir()

    _write_meta(tmp_path)

    seed_counter = 0
    for label in _LABELS:
        for seed in _SEEDS:
            features = list(PRE_FEATURE_NAMES)
            if label == "ctrl" and seed == 0:
                features = list(reversed(features))
            _train_tiny_booster(
                features,
                seed=seed_counter,
                path=str(boosters_dir / f"{label}_s{seed}.txt"),
            )
            seed_counter += 1

    return tmp_path


@pytest.fixture
def missing_booster_fixture_dir(tmp_path):
    """Fixture with only 14 boosters (ctrl_s4.txt is absent — booster count mismatch)."""
    boosters_dir = tmp_path / "boosters"
    boosters_dir.mkdir()

    _write_meta(tmp_path)

    seed_counter = 0
    for label in _LABELS:
        for seed in _SEEDS:
            if label == "ctrl" and seed == 4:
                seed_counter += 1
                continue  # deliberately omit this booster
            _train_tiny_booster(
                PRE_FEATURE_NAMES,
                seed=seed_counter,
                path=str(boosters_dir / f"{label}_s{seed}.txt"),
            )
            seed_counter += 1

    return tmp_path


# ---------------------------------------------------------------------------
# Wiring guard (H1 ImportError trap) — not real tests; import presence only
# ---------------------------------------------------------------------------


def test_promote_blend_importable():
    """promote_blend must be importable from tools.promote_model; removal fails collection."""
    assert promote_blend is not None


def test_feature_contract_error_importable():
    """FeatureContractError must be importable from tools.promote_model; removal fails collection."""
    assert FeatureContractError is not None


# ---------------------------------------------------------------------------
# End-to-end success — DB-backed (v3.2 faithful fixture)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_promote_blend_creates_registry_entry(v32_fixture_dir):
    """promote_blend() returns a ModelRegistry row with a valid PK."""
    from core.models import ModelRegistry

    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    assert entry.pk is not None
    assert ModelRegistry.objects.filter(pk=entry.pk).exists()


@pytest.mark.django_db
def test_promote_blend_records_correct_kind(v32_fixture_dir):
    """The promoted artifact kind is 'lightgbm_regression_blend'."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    assert entry.kind == "lightgbm_regression_blend"


@pytest.mark.django_db
def test_promote_blend_records_feature_list_in_binding_order(v32_fixture_dir):
    """feature_list round-trips with the 20 PRE_FEATURE_NAMES in binding order."""
    from core.models import ModelRegistry

    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    loaded = ModelRegistry.objects.get(pk=entry.pk)
    assert loaded.feature_list == PRE_FEATURE_NAMES
    assert loaded.feature_list[0] == "pre_eb10_sold_frac"
    assert loaded.feature_list[-1] == "pre_n_distinct_sellers"
    assert len(loaded.feature_list) == 20


@pytest.mark.django_db
def test_promote_blend_records_labels_seeds_manifest(v32_fixture_dir):
    """The labels/seeds manifest records 3 labels × 5 seeds = 15 booster paths."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    manifest = entry.labels_seeds_manifest
    assert manifest["labels"] == _LABELS
    assert manifest["seeds"] == _SEEDS
    for label in _LABELS:
        assert len(manifest["boosters"][label]) == 5
    total = sum(len(v) for v in manifest["boosters"].values())
    assert total == _N_BOOSTERS


@pytest.mark.django_db
def test_promote_blend_records_blend_transform_descriptor(v32_fixture_dir):
    """blend_transform_descriptor records the rank-average blend method."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    descriptor = entry.blend_transform_descriptor
    assert descriptor["method"] == "rank_average"
    assert descriptor["blend"] == "mean_of_3_percentile_ranks"
    assert descriptor["per_label"] == "seed_average_then_percentile_rank"
    assert descriptor["labels"] == _LABELS


@pytest.mark.django_db
def test_promote_blend_records_content_hashes_for_all_boosters(v32_fixture_dir):
    """artifact_content_hashes covers meta.json + all 15 booster files."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    hashes = entry.artifact_content_hashes
    assert "meta.json" in hashes
    for label in _LABELS:
        for seed in _SEEDS:
            key = f"boosters/{label}_s{seed}.txt"
            assert key in hashes, f"Missing hash for {key}"
    assert len(hashes) == _N_BOOSTERS + 1  # 15 boosters + meta.json


@pytest.mark.django_db
def test_promote_blend_records_model_version(v32_fixture_dir):
    """model_version is read from meta.json['name']."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    assert entry.model_version == "trilly_pregrad_v3_2_fixture"


@pytest.mark.django_db
def test_promote_blend_records_feature_set_version(v32_fixture_dir):
    """feature_set_version is read from meta.json['feature_set']."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    assert entry.feature_set_version == "enrich20_buyer_cohort"


@pytest.mark.django_db
def test_promote_blend_entry_is_inactive_by_default(v32_fixture_dir):
    """A freshly promoted model is is_active=False (no auto-activate, §5.3)."""
    entry = promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    assert entry.is_active is False


@pytest.mark.django_db
def test_promote_blend_idempotent_second_call_creates_second_row(v32_fixture_dir):
    """Calling promote_blend twice creates two independent registry rows (idempotent write)."""
    from core.models import ModelRegistry

    before = ModelRegistry.objects.count()
    promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    promote_blend(
        v32_fixture_dir,
        feature_set_columns=PRE_FEATURE_NAMES,
        feature_set_live_servable=PRE_FEATURE_NAMES,
    )
    assert ModelRegistry.objects.count() == before + 2


# ---------------------------------------------------------------------------
# Rejection — seeded feature-order / booster-count mismatch
# ---------------------------------------------------------------------------


def test_promote_blend_rejects_feature_order_mismatch(misorder_fixture_dir):
    """A booster with reversed feature order triggers FeatureContractError (BOOSTER MISMATCH)."""
    with pytest.raises(FeatureContractError):
        promote_blend(
            misorder_fixture_dir,
            feature_set_columns=PRE_FEATURE_NAMES,
            feature_set_live_servable=PRE_FEATURE_NAMES,
        )


def test_promote_blend_rejects_wrong_booster_count_missing_file(missing_booster_fixture_dir):
    """A missing booster file triggers FeatureContractError (BOOSTER COUNT mismatch)."""
    with pytest.raises(FeatureContractError):
        promote_blend(
            missing_booster_fixture_dir,
            feature_set_columns=PRE_FEATURE_NAMES,
            feature_set_live_servable=PRE_FEATURE_NAMES,
        )


def test_promote_blend_prints_remedy_on_feature_order_mismatch(misorder_fixture_dir, capsys):
    """REMEDY is printed to stdout when a booster has the wrong feature order."""
    with pytest.raises(FeatureContractError):
        promote_blend(
            misorder_fixture_dir,
            feature_set_columns=PRE_FEATURE_NAMES,
            feature_set_live_servable=PRE_FEATURE_NAMES,
        )
    captured = capsys.readouterr()
    assert "PROMOTION REFUSED" in captured.out
    assert "REMEDY" in captured.out


def test_promote_blend_prints_remedy_on_booster_count_mismatch(missing_booster_fixture_dir, capsys):
    """REMEDY is printed to stdout when the booster count is wrong."""
    with pytest.raises(FeatureContractError):
        promote_blend(
            missing_booster_fixture_dir,
            feature_set_columns=PRE_FEATURE_NAMES,
            feature_set_live_servable=PRE_FEATURE_NAMES,
        )
    captured = capsys.readouterr()
    assert "PROMOTION REFUSED" in captured.out
    assert "REMEDY" in captured.out
    assert "ctrl_s4.txt" in captured.out
