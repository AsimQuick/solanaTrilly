# ---
# module: core.tests.test_firehose_scoring_firehose
# sprint: sprint-14
# story: live-firehose-spine
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-19
# dependencies: pytest, core.firehose.spine, core.pregrad_features
# ---
"""Scoring-scheduler tests for the firehose spine (offline, RUN-TWICE IDENTICAL).

From a banked pre-grad tape for a mint (ReplaySource-style in-memory swaps):
  §1 assemble_pregrad_features produces the 20 PRE_FEATURE_NAMES.
  §2 a tiny fixture BlendScorer produces a deterministic blend score.
  §3 the WHOLE assemble->score path is RUN-TWICE IDENTICAL (determinism).
  §4 gate_passes evaluates the adaptive_topk / threshold cut.

No DB, no network, no real model — a stub scorer stands in for BlendScorer
(its interface: score_pool(list[dict]) -> [{blend_score, label_scores, label_ranks}]).
"""
from __future__ import annotations

from core.firehose.spine import (
    assemble_pregrad_features,
    gate_passes,
    score_pregrad,
)
from core.pregrad_features import PRE_FEATURE_NAMES

# A banked pre-grad tape for ONE mint: graduation at t0=1000; pre-grad swaps
# have block_time < t0 so rel < 0.  Mix of buyers/sellers for non-trivial cohort
# features.  Extended to 20 swaps to pass the US-76 P2.4 secondary gate (>=20).
_GRAD_BT = 1000
_TAPE = [
    {"block_time": 700, "slot": 1,  "signature": "s1",  "side": "buy",  "owner": "A", "vol_sol": 2.0, "price": 0.001},
    {"block_time": 710, "slot": 2,  "signature": "s2",  "side": "buy",  "owner": "B", "vol_sol": 1.0, "price": 0.0011},
    {"block_time": 720, "slot": 3,  "signature": "s3",  "side": "buy",  "owner": "C", "vol_sol": 3.0, "price": 0.0012},
    {"block_time": 730, "slot": 4,  "signature": "s4",  "side": "sell", "owner": "A", "vol_sol": 1.5, "price": 0.0013},
    {"block_time": 740, "slot": 5,  "signature": "s5",  "side": "buy",  "owner": "A", "vol_sol": 0.5, "price": 0.0014},
    {"block_time": 750, "slot": 6,  "signature": "s6",  "side": "buy",  "owner": "D", "vol_sol": 4.0, "price": 0.0015},
    {"block_time": 760, "slot": 7,  "signature": "s7",  "side": "buy",  "owner": "E", "vol_sol": 1.2, "price": 0.0016},
    {"block_time": 770, "slot": 8,  "signature": "s8",  "side": "buy",  "owner": "F", "vol_sol": 0.8, "price": 0.0017},
    {"block_time": 780, "slot": 9,  "signature": "s9",  "side": "sell", "owner": "B", "vol_sol": 0.5, "price": 0.0018},
    {"block_time": 790, "slot": 10, "signature": "s10", "side": "buy",  "owner": "G", "vol_sol": 2.1, "price": 0.0019},
    {"block_time": 800, "slot": 11, "signature": "s11", "side": "buy",  "owner": "H", "vol_sol": 0.9, "price": 0.0020},
    {"block_time": 810, "slot": 12, "signature": "s12", "side": "buy",  "owner": "I", "vol_sol": 1.5, "price": 0.0021},
    {"block_time": 820, "slot": 13, "signature": "s13", "side": "sell", "owner": "C", "vol_sol": 2.0, "price": 0.0022},
    {"block_time": 830, "slot": 14, "signature": "s14", "side": "buy",  "owner": "J", "vol_sol": 0.7, "price": 0.0023},
    {"block_time": 840, "slot": 15, "signature": "s15", "side": "buy",  "owner": "K", "vol_sol": 1.1, "price": 0.0024},
    {"block_time": 850, "slot": 16, "signature": "s16", "side": "buy",  "owner": "L", "vol_sol": 0.6, "price": 0.0025},
    {"block_time": 860, "slot": 17, "signature": "s17", "side": "buy",  "owner": "D", "vol_sol": 3.0, "price": 0.0026},
    {"block_time": 870, "slot": 18, "signature": "s18", "side": "sell", "owner": "E", "vol_sol": 0.4, "price": 0.0027},
    {"block_time": 880, "slot": 19, "signature": "s19", "side": "buy",  "owner": "M", "vol_sol": 1.8, "price": 0.0028},
    {"block_time": 890, "slot": 20, "signature": "s20", "side": "buy",  "owner": "N", "vol_sol": 0.3, "price": 0.0029},
]


class _StubScorer:
    """Deterministic stand-in for BlendScorer.

    blend_score = a fixed weighted sum of two features, squashed to (0,1].
    Pure: same features -> same score, every run.
    """

    labels = ["ctrl", "oracle", "liq"]

    def score_pool(self, features_list):
        out = []
        for f in features_list:
            raw = (
                0.4 * float(f.get("pre_diamond_frac", 0.0))
                + 0.3 * float(f.get("pre_top5_buyer_share", 0.0))
                + 0.3 * float(f.get("pre_repeat_buyer_frac", 0.0))
            )
            blend = max(0.0, min(1.0, raw))
            out.append(
                {
                    "label_scores": {label: blend for label in self.labels},
                    "label_ranks": {label: blend for label in self.labels},
                    "blend_score": blend,
                }
            )
        return out

    def score_single(self, features, ref_dist):
        return self.score_pool([features])[0]


def test_assemble_features_produces_all_20_pre_features():
    features = assemble_pregrad_features(_TAPE, _GRAD_BT)
    assert features is not None
    for name in PRE_FEATURE_NAMES:
        assert name in features, f"missing pre_* feature {name}"
    assert len(features) == len(PRE_FEATURE_NAMES)


def test_no_pregrad_swaps_returns_none():
    # All swaps post-grad (block_time >= grad_bt) -> rel >= 0 -> no pre-grad tape.
    post = [{**s, "block_time": _GRAD_BT + 10} for s in _TAPE]
    assert assemble_pregrad_features(post, _GRAD_BT) is None


def test_score_pregrad_deterministic():
    features = assemble_pregrad_features(_TAPE, _GRAD_BT)
    scorer = _StubScorer()
    r1 = score_pregrad(features, scorer=scorer)
    r2 = score_pregrad(features, scorer=scorer)
    assert r1 == r2
    assert 0.0 <= r1["blend_score"] <= 1.0


def test_assemble_then_score_run_twice_identical():
    """§3 — the full assemble->score path is byte-identical run-to-run."""
    scorer = _StubScorer()

    def _run():
        feats = assemble_pregrad_features(_TAPE, _GRAD_BT)
        return feats, score_pregrad(feats, scorer=scorer)

    f1, s1 = _run()
    f2, s2 = _run()
    assert f1 == f2, "feature assembly must be deterministic"
    assert s1 == s2, "scoring must be deterministic"


def test_gate_passes_threshold():
    assert gate_passes(0.9, gate="adaptive_topk", threshold=0.8) is True
    assert gate_passes(0.7, gate="adaptive_topk", threshold=0.8) is False
    assert gate_passes(0.8, gate="threshold", threshold=0.8) is True
