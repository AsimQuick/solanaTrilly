#!/usr/bin/env python3
# ---
# module: scripts.generate_golden_fixture_ac282
# sprint: sprint-7
# story: US-28 AC-28.2
# status: utility
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: solanabilly3/src/tape_microstructure
# purpose: |
#   ONE-TIME generator for core/tests/fixtures/golden_fixture_t0t2_15s.json.
#   Source of truth: solanabilly3/src/tape_microstructure.compute_features.
#   Run with:  python3 scripts/generate_golden_fixture_ac282.py
#   DO NOT re-run to "update" the fixture — re-vendor instead (AC-28.1 re-vendor-note).
#   Provenance mirrors: solanabilly3/scripts/make_microstructure_golden.py
# ---
"""
Generate the G1 microstructure golden fixture from synthetic swap sets that
exercise every code path in compute_features (all-buy sentinel, sell fraction,
monotonic/peak/trough price paths, bucket coverage, slope/accel computation,
late-bucket-ret, unique-trader counts).

Output: core/tests/fixtures/golden_fixture_t0t2_15s.json

The expected_features in each token entry are computed by
  solanabilly3/src/tape_microstructure.compute_features
so the G1 parity test (test_tape_microstructure_ac282.py) proves that the
vendored core/tape_microstructure.compute_features is byte-identical to the
source-of-record for every feature in the contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Source-of-truth: solanabilly3 module (the original, pre-sanction-edit version)
SOLANABILLY3_ROOT = Path("/Users/asim/NoIcloud/solanabilly3")
sys.path.insert(0, str(SOLANABILLY3_ROOT))
from src.tape_microstructure import (  # noqa: E402
    DEFAULT_BUCKET_S,
    DEFAULT_WINDOW_S,
    compute_features,
)

OUT = Path(__file__).resolve().parent.parent / "core" / "tests" / "fixtures" / "golden_fixture_t0t2_15s.json"

# ---------------------------------------------------------------------------
# 15 diverse synthetic token swap sets (deterministic — no RNG)
# Each exercises a distinct combination of code paths in compute_features.
# ---------------------------------------------------------------------------

W = DEFAULT_WINDOW_S  # 120
B = DEFAULT_BUCKET_S  # 15

TOKENS: list[tuple[str, list[dict]]] = [
    # 1 — all-buy, 2 trades in bucket 0 only
    ("MINT_01_allbuy_2t", [
        {"rel": 2.0, "price": 0.001, "side": "buy", "vol": 100.0, "owner": "W1"},
        {"rel": 8.0, "price": 0.0012, "side": "buy", "vol": 150.0, "owner": "W2"},
    ]),
    # 2 — mixed 5 trades, spread across 3 buckets
    ("MINT_02_mixed_5t", [
        {"rel": 5.0,  "price": 0.001,  "side": "buy",  "vol": 100.0, "owner": "W1"},
        {"rel": 20.0, "price": 0.0012, "side": "buy",  "vol": 200.0, "owner": "W2"},
        {"rel": 35.0, "price": 0.0015, "side": "buy",  "vol": 150.0, "owner": "W1"},
        {"rel": 60.0, "price": 0.0013, "side": "sell", "vol": 80.0,  "owner": "W3"},
        {"rel": 90.0, "price": 0.0014, "side": "buy",  "vol": 120.0, "owner": "W4"},
    ]),
    # 3 — all-buy, 15 trades across many buckets (high trade count)
    ("MINT_03_allbuy_15t", [
        {"rel": float(i * 8), "price": 0.001 + i * 0.0001, "side": "buy",
         "vol": 50.0 + i * 10, "owner": f"W{i % 5}"}
        for i in range(15) if i * 8 < W
    ]),
    # 4 — high sell fraction (7 sells, 3 buys)
    ("MINT_04_highsell", [
        {"rel": 10.0, "price": 0.002, "side": "buy",  "vol": 500.0, "owner": "W1"},
        {"rel": 20.0, "price": 0.0019, "side": "sell", "vol": 100.0, "owner": "W2"},
        {"rel": 30.0, "price": 0.0018, "side": "sell", "vol": 120.0, "owner": "W3"},
        {"rel": 40.0, "price": 0.0017, "side": "sell", "vol": 90.0,  "owner": "W4"},
        {"rel": 50.0, "price": 0.0016, "side": "sell", "vol": 110.0, "owner": "W5"},
        {"rel": 60.0, "price": 0.0022, "side": "buy",  "vol": 300.0, "owner": "W1"},
        {"rel": 70.0, "price": 0.0015, "side": "sell", "vol": 80.0,  "owner": "W2"},
        {"rel": 80.0, "price": 0.0014, "side": "sell", "vol": 70.0,  "owner": "W3"},
        {"rel": 90.0, "price": 0.0020, "side": "buy",  "vol": 400.0, "owner": "W6"},
        {"rel": 100.0,"price": 0.0013, "side": "sell", "vol": 60.0,  "owner": "W4"},
    ]),
    # 5 — monotonically increasing price (slope > 0, no drawdown)
    ("MINT_05_monotonic_up", [
        {"rel": 10.0, "price": 0.001,  "side": "buy", "vol": 100.0, "owner": "W1"},
        {"rel": 30.0, "price": 0.0015, "side": "buy", "vol": 120.0, "owner": "W2"},
        {"rel": 50.0, "price": 0.002,  "side": "buy", "vol": 80.0,  "owner": "W3"},
        {"rel": 70.0, "price": 0.003,  "side": "buy", "vol": 200.0, "owner": "W1"},
        {"rel": 90.0, "price": 0.005,  "side": "buy", "vol": 150.0, "owner": "W4"},
        {"rel": 110.0,"price": 0.008,  "side": "buy", "vol": 300.0, "owner": "W2"},
    ]),
    # 6 — price peak then dump (max_runup > 0, max_drawdown < 0)
    ("MINT_06_peak_dump", [
        {"rel": 5.0,  "price": 0.001,  "side": "buy",  "vol": 200.0, "owner": "W1"},
        {"rel": 15.0, "price": 0.003,  "side": "buy",  "vol": 500.0, "owner": "W2"},
        {"rel": 30.0, "price": 0.008,  "side": "buy",  "vol": 300.0, "owner": "W3"},
        {"rel": 45.0, "price": 0.010,  "side": "buy",  "vol": 100.0, "owner": "W4"},
        {"rel": 60.0, "price": 0.006,  "side": "sell", "vol": 400.0, "owner": "W1"},
        {"rel": 75.0, "price": 0.003,  "side": "sell", "vol": 600.0, "owner": "W2"},
        {"rel": 90.0, "price": 0.002,  "side": "sell", "vol": 200.0, "owner": "W3"},
        {"rel": 105.0,"price": 0.0015, "side": "sell", "vol": 150.0, "owner": "W5"},
    ]),
    # 7 — price dip then recovery (V-shape)
    ("MINT_07_vshape", [
        {"rel": 5.0,  "price": 0.005,  "side": "buy",  "vol": 100.0, "owner": "W1"},
        {"rel": 20.0, "price": 0.003,  "side": "sell", "vol": 200.0, "owner": "W2"},
        {"rel": 40.0, "price": 0.001,  "side": "sell", "vol": 300.0, "owner": "W3"},
        {"rel": 60.0, "price": 0.0015, "side": "buy",  "vol": 400.0, "owner": "W4"},
        {"rel": 80.0, "price": 0.004,  "side": "buy",  "vol": 500.0, "owner": "W1"},
        {"rel": 110.0,"price": 0.006,  "side": "buy",  "vol": 300.0, "owner": "W5"},
    ]),
    # 8 — late-only activity (first swap at rel=100, only 1 bucket active)
    ("MINT_08_late_activity", [
        {"rel": 100.0, "price": 0.002, "side": "buy",  "vol": 100.0, "owner": "W1"},
        {"rel": 105.0, "price": 0.003, "side": "buy",  "vol": 200.0, "owner": "W2"},
        {"rel": 110.0, "price": 0.002, "side": "sell", "vol": 50.0,  "owner": "W3"},
        {"rel": 115.0, "price": 0.004, "side": "buy",  "vol": 300.0, "owner": "W4"},
    ]),
    # 9 — single bucket, all in bucket 3 (rel 45-59)
    ("MINT_09_single_bucket3", [
        {"rel": 46.0, "price": 0.001, "side": "buy",  "vol": 100.0, "owner": "W1"},
        {"rel": 48.0, "price": 0.0012,"side": "buy",  "vol": 150.0, "owner": "W2"},
        {"rel": 52.0, "price": 0.0015,"side": "sell", "vol": 80.0,  "owner": "W3"},
        {"rel": 55.0, "price": 0.0013,"side": "buy",  "vol": 200.0, "owner": "W1"},
        {"rel": 58.0, "price": 0.0011,"side": "sell", "vol": 60.0,  "owner": "W4"},
    ]),
    # 10 — many unique traders (10 different wallets)
    ("MINT_10_many_traders", [
        {"rel": float(i * 10 + 5), "price": 0.001 + i * 0.0001,
         "side": "buy" if i % 3 != 0 else "sell",
         "vol": 100.0, "owner": f"WALLET_{i:03d}"}
        for i in range(10)
    ]),
    # 11 — few unique traders (2 wallets, many trades)
    ("MINT_11_few_traders", [
        {"rel": float(i * 7 + 1), "price": 0.002 + (i % 3) * 0.0005,
         "side": "buy" if i % 2 == 0 else "sell",
         "vol": 200.0, "owner": "WHALE" if i < 8 else "RETAIL"}
        for i in range(12) if i * 7 + 1 < W
    ]),
    # 12 — spread across all 8 buckets (one trade per bucket at mid-bucket)
    ("MINT_12_all_buckets", [
        {"rel": float(b * B + B // 2), "price": 0.001 * (1 + b * 0.1),
         "side": "buy" if b % 3 != 2 else "sell",
         "vol": 100.0 + b * 20, "owner": f"W{b % 4}"}
        for b in range(W // B)
    ]),
    # 13 — equal buy/sell volume (ratio = 1.0)
    ("MINT_13_equal_vol", [
        {"rel": 10.0, "price": 0.001,  "side": "buy",  "vol": 500.0, "owner": "W1"},
        {"rel": 25.0, "price": 0.0012, "side": "sell", "vol": 250.0, "owner": "W2"},
        {"rel": 40.0, "price": 0.0014, "side": "buy",  "vol": 300.0, "owner": "W3"},
        {"rel": 55.0, "price": 0.0013, "side": "sell", "vol": 300.0, "owner": "W4"},
        {"rel": 70.0, "price": 0.0015, "side": "buy",  "vol": 200.0, "owner": "W1"},
        {"rel": 85.0, "price": 0.0014, "side": "sell", "vol": 450.0, "owner": "W5"},
    ]),
    # 14 — very high volume, extreme price move (10x up)
    ("MINT_14_moonshot", [
        {"rel": 1.0,  "price": 0.001,   "side": "buy",  "vol": 1000.0, "owner": "W1"},
        {"rel": 5.0,  "price": 0.003,   "side": "buy",  "vol": 2000.0, "owner": "W2"},
        {"rel": 10.0, "price": 0.010,   "side": "buy",  "vol": 5000.0, "owner": "W3"},
        {"rel": 15.0, "price": 0.008,   "side": "sell", "vol": 1000.0, "owner": "W1"},
        {"rel": 20.0, "price": 0.012,   "side": "buy",  "vol": 8000.0, "owner": "W4"},
    ]),
    # 15 — swaps exactly at rel=0 and near rel=window_s-epsilon
    ("MINT_15_boundary", [
        {"rel": 0.0,    "price": 0.001,  "side": "buy",  "vol": 100.0, "owner": "W1"},
        {"rel": 0.001,  "price": 0.0011, "side": "buy",  "vol": 200.0, "owner": "W2"},
        {"rel": 60.0,   "price": 0.0013, "side": "sell", "vol": 50.0,  "owner": "W3"},
        {"rel": 119.0,  "price": 0.0015, "side": "buy",  "vol": 300.0, "owner": "W4"},
        {"rel": 119.999,"price": 0.0016, "side": "buy",  "vol": 100.0, "owner": "W5"},
        # These should be excluded (rel >= window_s):
        {"rel": 120.0,  "price": 0.002,  "side": "buy",  "vol": 999.0, "owner": "OUT"},
        {"rel": 121.0,  "price": 0.003,  "side": "buy",  "vol": 999.0, "owner": "OUT"},
    ]),
]


def main() -> int:
    tokens_out = []
    failed = []
    for mint, swaps in TOKENS:
        feat = compute_features(swaps, DEFAULT_WINDOW_S, DEFAULT_BUCKET_S)
        if feat is None:
            failed.append(mint)
            continue
        pre = [
            {"rel": s["rel"], "price": s["price"], "side": s["side"],
             "vol": s["vol"], "owner": s["owner"]}
            for s in swaps if 0 <= s["rel"] < DEFAULT_WINDOW_S and s.get("price") and s["price"] > 0
        ]
        tokens_out.append({
            "mint_address": mint,
            "swaps": pre,
            "expected_features": feat,
        })

    if failed:
        print(f"[WARN] {len(failed)} tokens returned None (excluded): {failed}", file=sys.stderr)

    fixture = {
        "contract": "solanabilly3/src/tape_microstructure.py :: compute_features",
        "vendored_to": "core/tape_microstructure.py :: compute_features",
        "window_s": DEFAULT_WINDOW_S,
        "bucket_s": DEFAULT_BUCKET_S,
        "float_tolerance": 1e-9,
        "regenerator": "solanabilly3/scripts/make_microstructure_golden.py",
        "provenance": (
            "Synthetic swap sets generated by scripts/generate_golden_fixture_ac282.py. "
            "Expected features computed from solanabilly3/src/tape_microstructure.compute_features "
            "(source-of-record). Frozen at AC-28.2 implementation (sprint-7). "
            "DO NOT update expected_features by hand — re-vendor and re-run the generator."
        ),
        "tokens": tokens_out,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(fixture, indent=2))

    n_tr = [t["expected_features"]["tape_n_trades"] for t in fixture["tokens"]]
    all_buy = sum(t["expected_features"]["tape_all_buy_window"] for t in fixture["tokens"])
    print(f"[golden] wrote {OUT}")
    print(f"  {len(fixture['tokens'])} tokens; n_trades spread {min(n_tr)}..{max(n_tr)}; "
          f"all-buy cases={all_buy}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
