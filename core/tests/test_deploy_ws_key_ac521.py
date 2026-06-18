# ---
# module: core.tests.test_deploy_ws_key_ac521
# sprint: sprint-11
# story: US-52 AC-52.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, base64
# ---
"""AC-52.1 — RFC-6455-valid Sec-WebSocket-Key in deploy.yml WS smoke-test.

Structural tests that ensure the WS smoke-test step in deploy.yml generates
a valid Sec-WebSocket-Key per RFC 6455 §4.1 (exactly 16 random bytes →
24-char base64), replacing the previous hardcoded 22-byte invalid value.

Tests:
  test_ws_key_not_hardcoded_invalid         — hardcoded invalid key is absent from deploy.yml
  test_ws_key_uses_urandom_16               — WS smoke-test uses os.urandom(16) for the key
  test_ws_key_urandom_16_produces_24_chars  — simulated urandom(16) → base64 → 24-char string
  test_ws_key_24_chars_is_rfc6455_valid     — 24-char base64 decodes to exactly 16 bytes
"""
import base64
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

# The old invalid hardcoded key value (base64 of 22-byte b"solanatrilly_ac483_key")
_INVALID_KEY_SRC = "b\"solanatrilly_ac483_key\""
_INVALID_KEY_B64 = "c29sYW5hdHJpbGx5X2FjNDgzX2tleQ=="


def test_ws_key_not_hardcoded_invalid():
    """deploy.yml must NOT contain the 22-byte RFC-6455-invalid hardcoded WS key (AC-52.1)."""
    content = DEPLOY_YML.read_text()
    assert _INVALID_KEY_SRC not in content, (
        "deploy.yml still contains the hardcoded 22-byte invalid Sec-WebSocket-Key source "
        f"({_INVALID_KEY_SRC!r}). Replace with os.urandom(16) per AC-52.1."
    )
    assert _INVALID_KEY_B64 not in content, (
        "deploy.yml still contains the hardcoded RFC-6455-invalid base64 key "
        f"({_INVALID_KEY_B64!r}). Replace with os.urandom(16) per AC-52.1."
    )


def test_ws_key_uses_urandom_16():
    """deploy.yml WS smoke-test generates the key from os.urandom(16) (AC-52.1).

    RFC 6455 §4.1 requires exactly 16 random bytes encoded as 24-char base64.
    Using os.urandom(16) at deploy time satisfies both the randomness and the
    length requirements.
    """
    content = DEPLOY_YML.read_text()
    assert "os.urandom(16)" in content, (
        "deploy.yml WS smoke-test must generate the Sec-WebSocket-Key via "
        "os.urandom(16) (RFC 6455 §4.1 — 16 random bytes → 24-char base64). "
        "Found no os.urandom(16) call in deploy.yml (AC-52.1)."
    )


def test_ws_key_urandom_16_produces_24_chars():
    """base64.b64encode(os.urandom(16)) always produces a 24-char ASCII string.

    This validates the mathematical invariant: 16 bytes × (4/3) = 21.3 → padded
    to 24 chars. If this ever fails, the key construction is broken.
    """
    import os
    for _ in range(8):
        key = base64.b64encode(os.urandom(16)).decode()
        assert len(key) == 24, (
            f"base64.b64encode(os.urandom(16)) must be 24 chars (RFC 6455 §4.1); got {len(key)}"
        )
        assert key.isascii(), "Sec-WebSocket-Key must be ASCII"


def test_ws_key_24_chars_is_rfc6455_valid():
    """A 24-char base64 key decodes to exactly 16 bytes — the RFC 6455 §4.1 requirement."""
    import os
    key = base64.b64encode(os.urandom(16)).decode()
    decoded = base64.b64decode(key)
    assert len(decoded) == 16, (
        f"A 24-char base64 key must decode to 16 bytes (RFC 6455 §4.1); got {len(decoded)}"
    )
