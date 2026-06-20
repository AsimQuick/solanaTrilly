# ---
# module: core.tests.test_pipeline_control_api_us79
# sprint: sprint-14
# story: US-79 (operator inference Start/Stop)
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-20
# dependencies: pytest, pytest-django
# ---
"""US-79 — pipeline control API (inference Start/Stop + status board).

Verifies the dashboard's inference controls: status reflects PipelineState, the
inference toggle flips firehose_active + scoring_enabled together, and it NEVER
touches trading_enabled (the capital gate stays observe/paper).
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.django_db


def test_status_reflects_pipeline_state(client):
    from core.models import PipelineState

    state = PipelineState.get()
    state.firehose_active = True
    state.scoring_enabled = True
    state.trading_enabled = False
    state.save()

    resp = client.get("/api/control/pipeline/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["firehose_active"] is True
    assert body["scoring_enabled"] is True
    assert body["trading_enabled"] is False
    assert body["inference_on"] is True
    assert "counts" in body
    assert "tokens" in body["counts"]
    assert "predictions" in body["counts"]


def test_inference_start_sets_flags(client):
    from core.models import PipelineState

    resp = client.post(
        "/api/control/inference/",
        data=json.dumps({"on": True}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["inference_on"] is True
    state = PipelineState.get()
    assert state.firehose_active is True
    assert state.scoring_enabled is True
    # capital gate untouched
    assert state.trading_enabled is False


def test_inference_stop_clears_firehose(client):
    from core.models import PipelineState

    s = PipelineState.get()
    s.firehose_active = True
    s.scoring_enabled = True
    s.trading_enabled = True  # pretend operator had enabled capital
    s.save()

    resp = client.post(
        "/api/control/inference/",
        data=json.dumps({"on": False}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["inference_on"] is False
    state = PipelineState.get()
    assert state.firehose_active is False
    # stopping inference must NOT touch the capital gate
    assert state.trading_enabled is True


def test_inference_requires_bool(client):
    resp = client.post(
        "/api/control/inference/",
        data=json.dumps({"on": "yes"}),
        content_type="application/json",
    )
    assert resp.status_code == 400

    resp2 = client.post(
        "/api/control/inference/",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp2.status_code == 400


def test_status_counts_are_ints(client):
    resp = client.get("/api/control/pipeline/")
    counts = resp.json()["counts"]
    assert isinstance(counts["tokens"], int)
    assert isinstance(counts["predictions"], int)
