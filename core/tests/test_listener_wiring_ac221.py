# ---
# module: core.tests.test_listener_wiring_ac221
# sprint: sprint-5
# story: US-22 AC-22.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-16
# dependencies: core.tape.birdeye_swap_source, core.tape.recorder, core.clock,
#               core.management.commands.run_listener, ast, pathlib
# ---
"""AC-22.1 — BirdeyeSwapSource wired into run_listener behind the DataSource seam.

Structural tests (no live connections):

  1. test_build_swap_recorder_returns_tape_recorder
       build_swap_recorder() returns a TapeRecorder instance.

  2. test_build_swap_recorder_uses_birdeye_swap_source
       The recorder's _source is a BirdeyeSwapSource.

  3. test_build_swap_recorder_uses_wall_clock
       The recorder's _clock is a WallClock.

  4. test_recorder_core_does_not_import_birdeye_swap_source
       AST-scan recorder.py — it must NOT import BirdeyeSwapSource or
       core.tape.birdeye_swap_source.  The seam holds.

  5. test_run_listener_imports_birdeye_swap_source
       AST-scan run_listener.py — it DOES import BirdeyeSwapSource (the
       concrete source lives only at the adapter/wiring layer).

  6. test_run_listener_imports_wall_clock
       AST-scan run_listener.py — it DOES import WallClock.

  7. test_core_tape_files_do_not_import_birdeye_swap_source
       AST-scan all .py files under core/tape/ EXCEPT birdeye_swap_source.py
       itself — none may import BirdeyeSwapSource or core.tape.birdeye_swap_source.
       Extends the US-2 guard to the new concrete source.
"""
import ast
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
TAPE_ROOT = REPO_ROOT / "core" / "tape"
RECORDER_FILE = TAPE_ROOT / "recorder.py"
RUN_LISTENER_FILE = (
    REPO_ROOT / "core" / "management" / "commands" / "run_listener.py"
)
BIRDEYE_SOURCE_FILE = TAPE_ROOT / "birdeye_swap_source.py"

# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _imports_name(py_file: Path, name: str) -> bool:
    """Return True if *py_file* imports *name* (class or module) anywhere in its AST."""
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (SyntaxError, OSError):
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Check module path match (e.g. "core.tape.birdeye_swap_source")
            if name in module:
                return True
            # Check imported names (e.g. "BirdeyeSwapSource", "WallClock")
            for alias in node.names:
                if alias.name == name:
                    return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if name in alias.name:
                    return True

    return False


# ---------------------------------------------------------------------------
# 1. build_swap_recorder returns TapeRecorder
# ---------------------------------------------------------------------------


def test_build_swap_recorder_returns_tape_recorder() -> None:
    """build_swap_recorder() must return a TapeRecorder instance."""
    from core.management.commands.run_listener import build_swap_recorder
    from core.tape.recorder import TapeRecorder

    recorder = build_swap_recorder(
        api_key="dummy_key",
        mint="DUMMY_MINT_1111111111111111111111111111111",
    )
    assert isinstance(recorder, TapeRecorder), (
        f"Expected TapeRecorder, got {type(recorder).__name__}"
    )


# ---------------------------------------------------------------------------
# 2. build_swap_recorder uses BirdeyeSwapSource
# ---------------------------------------------------------------------------


def test_build_swap_recorder_uses_birdeye_swap_source() -> None:
    """The recorder's _source must be a BirdeyeSwapSource instance."""
    from core.management.commands.run_listener import build_swap_recorder
    from core.tape.birdeye_swap_source import BirdeyeSwapSource

    recorder = build_swap_recorder(api_key="dummy_key", mint="DUMMY_MINT")
    assert isinstance(recorder._source, BirdeyeSwapSource), (
        f"Expected _source to be BirdeyeSwapSource, got {type(recorder._source).__name__}"
    )


# ---------------------------------------------------------------------------
# 3. build_swap_recorder uses WallClock
# ---------------------------------------------------------------------------


def test_build_swap_recorder_uses_wall_clock() -> None:
    """The recorder's _clock must be a WallClock instance."""
    from core.clock import WallClock
    from core.management.commands.run_listener import build_swap_recorder

    recorder = build_swap_recorder(api_key="dummy_key", mint="DUMMY_MINT")
    assert isinstance(recorder._clock, WallClock), (
        f"Expected _clock to be WallClock, got {type(recorder._clock).__name__}"
    )


# ---------------------------------------------------------------------------
# 4. recorder.py does NOT import BirdeyeSwapSource (seam guard)
# ---------------------------------------------------------------------------


def test_recorder_core_does_not_import_birdeye_swap_source() -> None:
    """AST guard: recorder.py must NOT import BirdeyeSwapSource.

    The recorder core depends only on the abstract DataSource interface.
    The concrete BirdeyeSwapSource lives exclusively in the adapter wiring
    (run_listener.py).  This test enforces that seam.
    """
    assert RECORDER_FILE.exists(), f"recorder.py not found at {RECORDER_FILE}"

    imports_class = _imports_name(RECORDER_FILE, "BirdeyeSwapSource")
    imports_module = _imports_name(RECORDER_FILE, "core.tape.birdeye_swap_source")

    assert not imports_class and not imports_module, (
        "recorder.py must NOT import BirdeyeSwapSource or core.tape.birdeye_swap_source. "
        "The DataSource seam requires the core recorder to depend only on the abstract "
        "DataSource interface (Principle #7 / PRD §4 / US-2 guard)."
    )


# ---------------------------------------------------------------------------
# 5. run_listener.py DOES import BirdeyeSwapSource (adapter wiring confirmed)
# ---------------------------------------------------------------------------


def test_run_listener_imports_birdeye_swap_source() -> None:
    """AST check: run_listener.py must import BirdeyeSwapSource.

    This confirms the concrete source is wired at the adapter layer
    (the listener/wiring boundary), as required by AC-22.1.
    """
    assert RUN_LISTENER_FILE.exists(), (
        f"run_listener.py not found at {RUN_LISTENER_FILE}"
    )

    assert _imports_name(RUN_LISTENER_FILE, "BirdeyeSwapSource"), (
        "run_listener.py must import BirdeyeSwapSource — "
        "the concrete source must be wired at the adapter/listener layer (AC-22.1)."
    )


# ---------------------------------------------------------------------------
# 6. run_listener.py DOES import WallClock
# ---------------------------------------------------------------------------


def test_run_listener_imports_wall_clock() -> None:
    """AST check: run_listener.py must import WallClock.

    This confirms the wall clock is wired at the adapter layer alongside
    the concrete BirdeyeSwapSource — both live ONLY in the wiring, never
    on the core recorder path.
    """
    assert RUN_LISTENER_FILE.exists(), (
        f"run_listener.py not found at {RUN_LISTENER_FILE}"
    )

    assert _imports_name(RUN_LISTENER_FILE, "WallClock"), (
        "run_listener.py must import WallClock — "
        "the wall clock must be wired at the adapter/listener layer (AC-22.1)."
    )


# ---------------------------------------------------------------------------
# 7. All core/tape/*.py files (except birdeye_swap_source.py itself) must NOT
#    import BirdeyeSwapSource — extends the US-2 guard to the new concrete source
# ---------------------------------------------------------------------------


def test_core_tape_files_do_not_import_birdeye_swap_source() -> None:
    """AST guard: core/tape/ files (except birdeye_swap_source.py) must not import it.

    Extends the US-2 static-analysis guard to cover the new concrete source.
    The BirdeyeSwapSource is a leaf adapter; no tape module should depend on it.
    """
    violations: list[str] = []

    for py_file in sorted(TAPE_ROOT.rglob("*.py")):
        # Skip the source file itself — it is allowed to define the class
        if py_file.resolve() == BIRDEYE_SOURCE_FILE.resolve():
            continue

        rel = py_file.relative_to(REPO_ROOT)

        if _imports_name(py_file, "BirdeyeSwapSource"):
            violations.append(
                f"{rel}: imports 'BirdeyeSwapSource' (forbidden in core/tape/ modules)"
            )
        if _imports_name(py_file, "core.tape.birdeye_swap_source"):
            violations.append(
                f"{rel}: imports 'core.tape.birdeye_swap_source' "
                "(forbidden in core/tape/ modules)"
            )

    assert not violations, (
        "core/tape/ modules (other than birdeye_swap_source.py itself) must not import "
        "BirdeyeSwapSource.  The concrete source is a leaf adapter; no other tape module "
        "should depend on it (US-2 guard extension / Principle #7).\n"
        "Violations:\n" + "\n".join(f"  {v}" for v in violations)
    )
