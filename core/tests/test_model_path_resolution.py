# ---
# file: test_model_path_resolution.py
# purpose: Regression test for the conventional model-path base resolution in
#          run_firehose.py. The v4 wallet-bank fallback and the v7 booster dir
#          are resolved as Path(__file__).resolve().parents[N] / "models" / ...
#          From <root>/core/management/commands/run_firehose.py the base MUST be
#          <root> (parents[3]) — where models/ is mounted (/app/models in the
#          container). parents[4] resolves to <root>/.. (= "/" in /app), so the
#          bank + v7 boosters were looked up at /models/... instead of
#          /app/models/... and silently zero-filled / failed to load. The
#          offline suite missed it because boosters are host-only (absent in CI),
#          so the load path was never exercised with real files. This test pins
#          the base to the repo root regardless of booster presence.
# story: US-94 (deploy-surfaced model-path off-by-one)
# created-by: dev-team
# ---
from pathlib import Path

import core.management.commands.run_firehose as rf


def test_conventional_model_base_is_repo_root_not_one_above() -> None:
    """parents[3] of run_firehose.py is the repo root that contains core/ + models/.

    Catches the parents[4] off-by-one: parents[4] is one level ABOVE the root and
    does NOT contain the core/ package, so models/ lookups silently miss.
    """
    rf_file = Path(rf.__file__).resolve()

    base = rf_file.parents[3]
    assert (base / "core" / "management" / "commands" / "run_firehose.py").is_file(), (
        f"parents[3] must be the repo root containing the core/ package; got {base}"
    )

    # The buggy value (parents[4]) is one above the root and must NOT contain the app.
    above = rf_file.parents[4]
    assert not (above / "core" / "management" / "commands" / "run_firehose.py").is_file(), (
        "parents[4] resolves into the app tree — the off-by-one has regressed"
    )
