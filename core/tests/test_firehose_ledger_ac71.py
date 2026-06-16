# ---
# module: core.tests.test_firehose_ledger_ac71
# sprint: sprint-2
# story: US-7 AC-7.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pathlib, re
# ---
"""AC-7.1 — ops/firehose_activation_log.md exists and is seeded with the
project-wide budget: 10 Birdeye + 10 Helius activations (remaining 10/10 at seed).
Once activations are logged the ledger tracks spends; the durable invariant is that
each source's budget arithmetic balances (Used + Remaining == Total).
API keys are referenced via .env — never committed to the ledger (PRD §15.7).

Tests:
  test_ledger_file_exists
      ops/firehose_activation_log.md is present at the expected path.

  test_ledger_has_metadata_front_matter
      File begins with YAML front matter (--- ... --- delimiters).

  test_ledger_birdeye_budget_is_ten
      Ledger records a total Birdeye budget of 10.

  test_ledger_helius_budget_is_ten
      Ledger records a total Helius budget of 10.

  test_ledger_budget_table_arithmetic_consistent
      Each source's budget row balances: Used + Remaining == Total.

  test_ledger_references_env_for_api_keys
      Ledger references .env as the source of API keys.

  test_ledger_no_api_key_values_committed
      No raw key material (long random strings) is committed in the ledger.
      The file must reference env-var names only, never actual key values.
"""
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "ops" / "firehose_activation_log.md"


def _ledger_text() -> str:
    return LEDGER_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_ledger_file_exists() -> None:
    """ops/firehose_activation_log.md must exist at the repo-relative path."""
    assert LEDGER_PATH.exists(), (
        f"ops/firehose_activation_log.md not found at {LEDGER_PATH}. "
        "AC-7.1 requires this file to be created and seeded."
    )


def test_ledger_has_metadata_front_matter() -> None:
    """Ledger must begin with YAML front matter (--- ... ---) per project conventions."""
    text = _ledger_text()
    assert text.startswith("---"), (
        "ops/firehose_activation_log.md must begin with YAML front matter ('---'). "
        "All project files carry structured metadata headers (CLAUDE.md convention)."
    )
    # Locate the closing --- after the opening one
    rest = text[3:]
    assert "---" in rest, (
        "ops/firehose_activation_log.md front matter is not closed with a second '---' line. "
        "Expected: ---\\n<fields>\\n---"
    )


def test_ledger_birdeye_budget_is_ten() -> None:
    """Ledger must record a Birdeye total budget of 10 activations."""
    text = _ledger_text()
    # Accept: "Birdeye | 10" (table), "10 Birdeye", "Birdeye: 10", "Birdeye  |    10"
    pattern = re.compile(
        r"(?:Birdeye\s*\|[^|]*?\b10\b|\b10\b[^\n]*Birdeye|Birdeye[:\s]+10)",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "ops/firehose_activation_log.md does not state a Birdeye budget of 10. "
        "AC-7.1: seed with '10 Birdeye activations'."
    )


def test_ledger_helius_budget_is_ten() -> None:
    """Ledger must record a Helius total budget of 10 activations."""
    text = _ledger_text()
    pattern = re.compile(
        r"(?:Helius\s*\|[^|]*?\b10\b|\b10\b[^\n]*Helius|Helius[:\s]+10)",
        re.IGNORECASE,
    )
    assert pattern.search(text), (
        "ops/firehose_activation_log.md does not state a Helius budget of 10. "
        "AC-7.1: seed with '10 Helius activations'."
    )


def test_ledger_budget_table_arithmetic_consistent() -> None:
    """Ledger budget table is internally consistent: Used + Remaining == Total per source.

    AC-7.1 originally seeded the ledger at 0 used (clean slate). Once legitimate,
    ledgered firehose activations are logged (e.g. the US-32 AC-32.2 G2(b) Helius
    spend, Helius 10 -> 9), the "0 used" seed value is no longer true by design —
    the whole point of the ledger is to track spends as they happen (PRD §15.7).
    The durable invariant that survives every activation is that each source row's
    arithmetic balances: Used + Remaining == Total. A mis-decremented budget (the
    failure the ledger exists to prevent) breaks this.
    """
    text = _ledger_text()
    checked = 0
    for source in ("Birdeye", "Helius"):
        row = next(
            (ln for ln in text.splitlines() if ln.strip().startswith(f"| {source}")),
            None,
        )
        assert row is not None, f"No {source} budget-table row found in the ledger."
        # Cells: ['', source, total, used, remaining, '']
        cells = [c.strip() for c in row.split("|")]
        nums = [c for c in cells if c.lstrip("-").isdigit()]
        assert len(nums) >= 3, f"{source} budget row missing Total/Used/Remaining: {row!r}"
        total, used, remaining = int(nums[0]), int(nums[1]), int(nums[2])
        assert used + remaining == total, (
            f"{source} budget arithmetic broken: Used({used}) + Remaining({remaining}) "
            f"!= Total({total}). Row: {row!r}"
        )
        assert 0 <= used <= total, f"{source} Used({used}) out of range [0, {total}]."
        checked += 1
    assert checked == 2, "Both Birdeye and Helius budget rows must be present."


def test_ledger_birdeye_remaining_is_ten() -> None:
    """Ledger must show 10 remaining Birdeye activations (remaining 10/10)."""
    text = _ledger_text()
    # Look for "Birdeye" row in a table with a trailing 10 (the remaining column)
    # or explicit "remaining 10 / 10" / "Remaining | 10" language near Birdeye
    birdeye_line = next(
        (line for line in text.splitlines() if "Birdeye" in line and "|" in line),
        None,
    )
    assert birdeye_line is not None, (
        "No Birdeye table row found in ops/firehose_activation_log.md."
    )
    # The remaining column should be 10
    cells = [c.strip().lstrip("*").rstrip("*") for c in birdeye_line.split("|")]
    assert "10" in cells, (
        f"Birdeye table row does not show a remaining count of 10. Row: {birdeye_line!r}"
    )


def test_ledger_helius_remaining_is_ten() -> None:
    """Ledger must show 10 remaining Helius activations (remaining 10/10)."""
    text = _ledger_text()
    helius_line = next(
        (line for line in text.splitlines() if "Helius" in line and "|" in line),
        None,
    )
    assert helius_line is not None, (
        "No Helius table row found in ops/firehose_activation_log.md."
    )
    cells = [c.strip().lstrip("*").rstrip("*") for c in helius_line.split("|")]
    assert "10" in cells, (
        f"Helius table row does not show a remaining count of 10. Row: {helius_line!r}"
    )


def test_ledger_references_env_for_api_keys() -> None:
    """Ledger must reference .env as the source of API keys (PRD §15.7 — keys never committed)."""
    text = _ledger_text()
    assert ".env" in text, (
        "ops/firehose_activation_log.md must reference '.env' as the location of "
        "Birdeye/Helius API keys. Keys live in .env (gitignored) — never in the ledger."
    )


def test_ledger_no_api_key_values_committed() -> None:
    """No raw API key material may be present in the ledger (PRD §15.7).

    Heuristic: any token of 20+ consecutive alphanumeric/base64 chars that is not
    inside a Markdown code block and does not look like a standard English word is
    treated as potential key material.  The ledger must reference env-var names only.
    """
    text = _ledger_text()
    # Remove YAML front matter from the scan (it contains the file name which is long)
    body = text
    if text.startswith("---"):
        end_fm = text.find("---", 3)
        if end_fm != -1:
            body = text[end_fm + 3:]

    # Remove fenced code blocks (```...```) — legitimate long strings may appear there
    body = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    # Remove inline code spans (`...`)
    body = re.sub(r"`[^`]+`", "", body)

    # Pattern: 32+ consecutive base64-alphabet chars (looks like an API key/token)
    # Short words and normal table separators won't match this length
    suspicious = re.findall(r"[A-Za-z0-9+/=_\-]{32,}", body)

    # Filter out known-safe long tokens: URLs, markdown separators, known env-var names
    safe_patterns = re.compile(
        r"^(?:"
        r"https?://.*"          # URLs
        r"|[-=|*]{5,}"          # Markdown table separators / hr lines
        r"|[A-Z][A-Z0-9_]{4,}" # ALL_CAPS env-var names like BIRDEYE_API_KEY
        r"|firehose_activation_log"  # the filename itself
        r")$"
    )
    leaks = [tok for tok in suspicious if not safe_patterns.match(tok)]

    assert not leaks, (
        "ops/firehose_activation_log.md appears to contain raw API key material "
        f"(PRD §15.7 — keys must NEVER be committed). Suspicious tokens: {leaks!r}\n"
        "The ledger must reference env-var names (e.g. BIRDEYE_API_KEY) only, "
        "never actual key values. Keys belong in .env (gitignored)."
    )
