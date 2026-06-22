# ---
# module: copytrade.tests.test_curvestage_coverage
# epic: EPIC-copy-curvestage-integration
# status: implemented
# created-by: claude (live-run operator)
# last-updated: 2026-06-22
# ---
"""Branch coverage for the curvestage HTTP client, settler, retrain guard, and
classifier scoring logic (all offline, no network, no model artifact)."""
from __future__ import annotations

import json
import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import pytest

# ---------------------------------------------------------------------------
# birdeye_tape.fetch_token_tape — pagination + error paths (mock urlopen)
# ---------------------------------------------------------------------------


def _urlopen_returning(payload: dict):
    cm = mock.MagicMock()
    cm.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return cm


def test_fetch_token_tape_one_page():
    from copytrade.birdeye_tape import fetch_token_tape

    page = {"data": {"items": [
        {"blockUnixTime": 100, "txHash": "a"},
        {"blockUnixTime": 110, "txHash": "b"},
        {"blockUnixTime": 999, "txHash": "c"},  # past t_to -> stops the scan
    ]}}
    with mock.patch("urllib.request.urlopen", return_value=_urlopen_returning(page)):
        items = fetch_token_tape("M", 50, 200, api_key="k")
    assert [i["txHash"] for i in items] == ["a", "b"]  # c (bt=999>200) excluded


def test_fetch_token_tape_missing_key():
    from copytrade.birdeye_tape import fetch_token_tape

    assert fetch_token_tape("M", 0, 100, api_key="") == []


def test_fetch_token_tape_http_error_returns_empty():
    import urllib.error

    from copytrade.birdeye_tape import fetch_token_tape

    err = urllib.error.HTTPError("u", 404, "nf", {}, None)
    with mock.patch("urllib.request.urlopen", side_effect=err):
        assert fetch_token_tape("M", 0, 100, api_key="k") == []


def test_fetch_token_tape_empty_page():
    from copytrade.birdeye_tape import fetch_token_tape

    with mock.patch("urllib.request.urlopen", return_value=_urlopen_returning({"data": {"items": []}})):
        assert fetch_token_tape("M", 0, 100, api_key="k") == []


# ---------------------------------------------------------------------------
# pgrad_classifier — predict/passes logic with an injected fake booster
# ---------------------------------------------------------------------------


def test_pgrad_classifier_predict_and_passes():
    from copytrade.pgrad_classifier import PgradClassifier

    clf = PgradClassifier(model_dir="/nonexistent")
    clf._booster = mock.Mock()
    clf._booster.predict.return_value = [0.42]
    clf._feats = ["a", "b"]
    clf._threshold = 0.20
    feats = {"a": 1.0, "b": "bad"}  # non-numeric -> defaults to 0.0
    passed, p = clf.passes(feats)
    assert p == pytest.approx(0.42) and passed is True
    clf._threshold = 0.50
    assert clf.passes(feats)[0] is False


def test_pgrad_classifier_predict_before_load_raises():
    from copytrade.pgrad_classifier import PgradClassifier

    with pytest.raises(RuntimeError):
        PgradClassifier().predict({})


# ---------------------------------------------------------------------------
# curvestage_train — lake-age guard (no-op until >=7d)
# ---------------------------------------------------------------------------


def test_lake_age_days(tmp_path):
    from copytrade.curvestage_train import _lake_age_days

    (tmp_path / "dt=2026-06-20").mkdir()
    (tmp_path / "dt=2026-06-22").mkdir()
    (tmp_path / "dt=1977-09-01").mkdir()  # slot-bug artifact, ignored
    assert _lake_age_days(str(tmp_path)) == 2


def test_retrain_noop_when_lake_young(tmp_path):
    from copytrade.curvestage_train import retrain_from_lake

    (tmp_path / "dt=2026-06-21").mkdir()
    (tmp_path / "dt=2026-06-22").mkdir()
    res = retrain_from_lake(str(tmp_path), str(tmp_path))
    assert res["retrained"] is False and res["reason"] == "lake_too_young"


# ---------------------------------------------------------------------------
# settle_due_curvestage_positions — close paths (graduated / timer / reject / void)
# ---------------------------------------------------------------------------


def _open_pos(cohort, mint, entry_dt):
    from copytrade.models import CopytradePosition

    return CopytradePosition.objects.create(
        cohort_id=cohort, mint=mint, trigger_wallet="W", status=CopytradePosition.STATUS_OPEN,
        mode=CopytradePosition.MODE_OBSERVE, entry_ts=entry_dt, size_usd=25.0, sol_in=0.18,
    )


@pytest.mark.django_db(transaction=True)
def test_settle_graduated_writes_pnl_and_graduation_reason():
    from copytrade.curvestage_engine import settle_due_curvestage_positions
    from copytrade.models import CopytradePosition

    cohort = "copy_x_curvestage"
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    buy_dt = now - timedelta(minutes=5)
    pos = _open_pos(cohort, "Mgrad", buy_dt)
    gts = int(buy_dt.timestamp()) + 120
    settled = {"reason": "ok", "pnl_pct": 28.0, "entry": 1.0, "exit_t": float(gts), "graduated": True}
    with mock.patch("copytrade.curvestage_engine._graduation_bt", return_value=gts), \
         mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[{"x": 1}]), \
         mock.patch("copytrade.curvestage_engine.birdeye_items_to_owner_tape", return_value=[(1, 1.0, 1.0, "buy")]), \
         mock.patch("copytrade.curvestage_engine.settle_grad", return_value=settled):
        closed = settle_due_curvestage_positions(now, cohort_id=cohort)
    pos.refresh_from_db()
    assert len(closed) == 1
    assert pos.status == CopytradePosition.STATUS_CLOSED
    assert pos.exit_reason == CopytradePosition.EXIT_GRADUATION
    assert pos.realized_pnl_pct == pytest.approx(28.0)
    assert pos.realized_pnl_sol == pytest.approx(0.18 * 0.28)


@pytest.mark.django_db(transaction=True)
def test_settle_rejected_fill_writes_null_pnl():
    from copytrade.curvestage_engine import settle_due_curvestage_positions
    from copytrade.models import CopytradePosition

    cohort = "copy_x_curvestage"
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    pos = _open_pos(cohort, "Mslip", now - timedelta(hours=1))  # past window -> due
    with mock.patch("copytrade.curvestage_engine._graduation_bt", return_value=None), \
         mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[{"x": 1}]), \
         mock.patch("copytrade.curvestage_engine.birdeye_items_to_owner_tape", return_value=[(1, 1.0, 1.0, "buy")]), \
         mock.patch("copytrade.curvestage_engine.settle_grad", return_value={"reason": "slip6002", "slip": 0.3}):
        settle_due_curvestage_positions(now, cohort_id=cohort)
    pos.refresh_from_db()
    assert pos.status == CopytradePosition.STATUS_CLOSED
    assert pos.exit_reason == CopytradePosition.EXIT_ENTRY_REJECTED
    assert pos.realized_pnl_pct is None


@pytest.mark.django_db(transaction=True)
def test_settle_skips_when_not_graduated_and_in_window():
    from copytrade.curvestage_engine import settle_due_curvestage_positions
    from copytrade.models import CopytradePosition

    cohort = "copy_x_curvestage"
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    pos = _open_pos(cohort, "Mwait", now - timedelta(minutes=2))  # within 30min window, not graduated
    with mock.patch("copytrade.curvestage_engine._graduation_bt", return_value=None):
        closed = settle_due_curvestage_positions(now, cohort_id=cohort)
    pos.refresh_from_db()
    assert closed == [] and pos.status == CopytradePosition.STATUS_OPEN  # still waiting


@pytest.mark.django_db(transaction=True)
def test_settle_voids_when_tape_unavailable_far_past():
    from copytrade.curvestage_engine import settle_due_curvestage_positions
    from copytrade.models import CopytradePosition

    cohort = "copy_x_curvestage"
    now = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)
    pos = _open_pos(cohort, "Mvoid", now - timedelta(hours=3))  # far past, no tape
    with mock.patch("copytrade.curvestage_engine._graduation_bt", return_value=None), \
         mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[]):
        settle_due_curvestage_positions(now, cohort_id=cohort)
    pos.refresh_from_db()
    assert pos.status == CopytradePosition.STATUS_CLOSED
    assert pos.exit_reason == CopytradePosition.EXIT_VOID


# ---------------------------------------------------------------------------
# extra coverage: classifier load(), HTTP retry branches, mature-lake path
# ---------------------------------------------------------------------------


def test_pgrad_classifier_load(tmp_path):
    from copytrade.pgrad_classifier import PgradClassifier

    (tmp_path / "pgrad_meta.json").write_text(
        json.dumps({"features": ["a", "b"], "pgrad_threshold_frozen": 0.31})
    )
    (tmp_path / "pgrad_lgbm.txt").write_text("model-bytes")
    with mock.patch.dict("sys.modules", {"lightgbm": mock.Mock()}):
        clf = PgradClassifier(str(tmp_path)).load()
    assert clf.threshold == pytest.approx(0.31)
    assert clf.meta["features"] == ["a", "b"]


def test_fetch_token_tape_retries_429_then_succeeds():
    import urllib.error

    from copytrade.birdeye_tape import fetch_token_tape

    e429 = urllib.error.HTTPError("u", 429, "rate", {"Retry-After": "0"}, None)
    page = _urlopen_returning({"data": {"items": [{"blockUnixTime": 10, "txHash": "a"}]}})
    with mock.patch("time.sleep"), mock.patch("urllib.request.urlopen", side_effect=[e429, page]):
        items = fetch_token_tape("M", 0, 100, api_key="k")
    assert [i["txHash"] for i in items] == ["a"]


def test_fetch_token_tape_retries_500_then_succeeds():
    import urllib.error

    from copytrade.birdeye_tape import fetch_token_tape

    e500 = urllib.error.HTTPError("u", 503, "down", {}, None)
    page = _urlopen_returning({"data": {"items": [{"blockUnixTime": 10, "txHash": "z"}]}})
    with mock.patch("time.sleep"), mock.patch("urllib.request.urlopen", side_effect=[e500, page]):
        items = fetch_token_tape("M", 0, 100, api_key="k")
    assert [i["txHash"] for i in items] == ["z"]


def test_retrain_mature_lake_pending_build(tmp_path):
    from copytrade.curvestage_train import retrain_from_lake

    (tmp_path / "dt=2026-06-10").mkdir()
    (tmp_path / "dt=2026-06-22").mkdir()  # 12 days -> mature
    res = retrain_from_lake(str(tmp_path), str(tmp_path))
    assert res["reason"] == "lake_retrain_pending_build" and res["lake_age_days"] == 12


# ---------------------------------------------------------------------------
# margin coverage: gate edge paths, _graduation_bt, URLError, classifier props
# ---------------------------------------------------------------------------


def _cs_event(mint="Mpump", program=""):
    return mock.Mock(mint=mint, wallet="W1", sol_amount=2.0, block_time=1000,
                     tx_type="buy", raw={"program": program})


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_gate2_curve_frac_blocks():
    from copytrade.curvestage_engine import handle_curvestage_buy

    feats = {"curve_frac": 0.7, "pre_sol_in": 59.5}  # > 0.6 -> gate 2 fail
    otr = [(1, 1.0, 1.0, "buy", "o", 0.1)]
    with mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[{"x": 1}]), \
         mock.patch("copytrade.curvestage_engine.birdeye_items_to_owner_tape", return_value=otr), \
         mock.patch("copytrade.curvestage_engine.entry_features", return_value=feats):
        assert handle_curvestage_buy(_cs_event(), 150.0, "copy_x_curvestage", "s1") is None


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_feats_none_skips():
    from copytrade.curvestage_engine import handle_curvestage_buy

    with mock.patch("copytrade.curvestage_engine.fetch_token_tape", return_value=[{"x": 1}]), \
         mock.patch("copytrade.curvestage_engine.birdeye_items_to_owner_tape", return_value=[]), \
         mock.patch("copytrade.curvestage_engine.entry_features", return_value=None):
        assert handle_curvestage_buy(_cs_event(), 150.0, "copy_x_curvestage", "s1") is None


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_dedupe_existing_open():
    from copytrade.curvestage_engine import handle_curvestage_buy
    from copytrade.models import CopytradePosition

    CopytradePosition.objects.create(
        cohort_id="copy_x_curvestage", mint="Mdup", trigger_wallet="W0",
        status=CopytradePosition.STATUS_OPEN, mode=CopytradePosition.MODE_OBSERVE,
    )
    # No Birdeye mock needed: dedupe short-circuits before the fetch.
    assert handle_curvestage_buy(_cs_event(mint="Mdup"), 150.0, "copy_x_curvestage", "s1") is None


@pytest.mark.django_db(transaction=True)
def test_handle_curvestage_post_grad_buy_skipped_via_recorder_graduation():
    """Bug fix: a buy AFTER graduation (per the recorder's graduated_block_time) is
    rejected even though the Helius wallet event carries no `program` field (so the
    cheap is_on_bonding_curve gate defaults to on-curve).  The buy_ts is 1000 and
    the token graduated at 934 (66s earlier — mirrors the live DoeeM6LF miss): no
    position is booked and the Birdeye tape fetch is never reached.
    """
    from copytrade.curvestage_engine import handle_curvestage_buy
    from copytrade.models import CopytradePosition

    fetch = mock.Mock()
    with mock.patch("copytrade.curvestage_engine._graduation_bt", return_value=934), \
         mock.patch("copytrade.curvestage_engine.fetch_token_tape", fetch):
        out = handle_curvestage_buy(_cs_event(mint="Mpostgrad"), 150.0, "copy_x_curvestage", "s1")
    assert out is None, "a post-grad buy must be skipped, not booked as a spurious loss"
    fetch.assert_not_called()  # gate 1b short-circuits before any Birdeye spend
    assert not CopytradePosition.objects.filter(mint="Mpostgrad").exists()


def test_graduation_bt_reads_token():
    from copytrade.curvestage_engine import _graduation_bt

    with mock.patch("core.models.Token.objects") as objs:
        objs.filter.return_value.values.return_value.first.return_value = {"graduated_block_time": 1782000000}
        assert _graduation_bt("Mg") == 1782000000
        objs.filter.return_value.values.return_value.first.return_value = None
        assert _graduation_bt("Mnotexist") is None


def test_fetch_token_tape_urlerror_exhausts():
    import urllib.error

    from copytrade.birdeye_tape import fetch_token_tape

    with mock.patch("time.sleep"), \
         mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        assert fetch_token_tape("M", 0, 100, api_key="k") == []


def test_get_pgrad_classifier_singleton_and_props():
    import copytrade.pgrad_classifier as pc

    fake = mock.Mock()
    fake.threshold = 0.2
    fake.meta = {"k": "v"}
    with mock.patch.object(pc, "_singleton", fake):
        got = pc.get_pgrad_classifier()
    assert got is fake and got.threshold == 0.2 and got.meta == {"k": "v"}
