# ---
# module: core.tests.test_deploy_ws_key_ac522
# sprint: sprint-11
# story: US-52 AC-52.2
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pathlib, base64, os
# ---
"""AC-52.2 — Hardened structural test: validates the deploy.yml WS key's RFC-6455 length at the
point it is generated/sent, not merely the presence of the smoke-test step.

A green test can NO LONGER mask a 400-ing handshake: these tests extract the 'key = ...'
assignment from deploy.yml's Python heredoc (PYEOF block), execute it, and assert the
resulting key is exactly 24 chars (base64) and decodes to exactly 16 bytes (RFC 6455 §4.1).
Any reversion to a hardcoded value of the wrong length (e.g. the old 22-byte invalid key
'c29sYW5hdHJpbGx5X2FjNDgzX2tleQ==', which caused Daphne to return HTTP 400) will fail
tests 2 and 3.

Tests:
  test_ws_key_assignment_exists_in_pyeof_block     — key = ... exists in the Python heredoc
  test_ws_key_evaluates_to_24_char_string          — executed key is exactly 24 chars (RFC 6455)
  test_ws_key_decodes_to_exactly_16_bytes          — executed key decodes to exactly 16 bytes
"""
import base64
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_YML = REPO_ROOT / ".github" / "workflows" / "deploy.yml"

_PYEOF_OPEN = "<<'PYEOF'"
_PYEOF_CLOSE = "PYEOF"
_KEY_PREFIX = "key = "


def _extract_key_assignment() -> str | None:
    """Return the 'key = ...' line from deploy.yml's Python PYEOF heredoc, or None if absent."""
    lines = DEPLOY_YML.read_text().splitlines()
    in_pyeof = False
    for line in lines:
        stripped = line.strip()
        if _PYEOF_OPEN in stripped:
            in_pyeof = True
            continue
        if in_pyeof and stripped == _PYEOF_CLOSE:
            break
        if in_pyeof and stripped.startswith(_KEY_PREFIX):
            return stripped
    return None


def _evaluate_key_assignment(key_assignment: str) -> str | None:
    """Execute key_assignment in a sandboxed namespace and return the resulting key string."""
    namespace: dict = {"base64": base64, "os": os}
    exec(key_assignment, namespace)  # noqa: S102
    return namespace.get("key")


def test_ws_key_assignment_exists_in_pyeof_block():
    """deploy.yml WS smoke-test Python heredoc must contain a 'key = ' assignment (AC-52.2).

    If the assignment is absent, the smoke-test sends no Sec-WebSocket-Key and the WS
    upgrade will fail. This guards against accidental removal of the key generation line.
    """
    key_assignment = _extract_key_assignment()
    assert key_assignment is not None, (
        "No 'key = ' assignment found in deploy.yml's Python PYEOF heredoc. "
        "The WS smoke-test step must generate a Sec-WebSocket-Key per RFC 6455 §4.1 (AC-52.2). "
        f"Expected a 'key = ...' line between '{_PYEOF_OPEN}' and '{_PYEOF_CLOSE}' in deploy.yml."
    )


def test_ws_key_evaluates_to_24_char_string():
    """Executing the deploy.yml key assignment must produce a 24-char base64 string (AC-52.2).

    RFC 6455 §4.1 requires exactly 16 random bytes encoded as base64 — always 24 chars.
    Any key shorter or longer than 24 chars will cause Daphne to reject the WS upgrade
    with HTTP 400 ('bad Sec-WebSocket-Key (length must be 24 ASCII chars)').

    This test fails if the key is reverted to a hardcoded value of the wrong length, e.g.:
      - 'c29sYW5hdHJpbGx5X2FjNDgzX2tleQ==' (old invalid key, 33 chars, decodes to 22 bytes)
      - any other non-24-char literal
    """
    key_assignment = _extract_key_assignment()
    assert key_assignment is not None, (
        "No 'key = ' assignment found in deploy.yml PYEOF block — cannot validate key length."
    )
    key = _evaluate_key_assignment(key_assignment)
    assert key is not None, (
        f"Key assignment {key_assignment!r} did not produce a 'key' variable in the namespace."
    )
    assert isinstance(key, str), (
        f"Sec-WebSocket-Key must be a str; got {type(key).__name__} from {key_assignment!r}."
    )
    assert len(key) == 24, (
        f"deploy.yml WS key must be exactly 24 chars (RFC 6455 §4.1 — base64 of 16 bytes); "
        f"got {len(key)} chars from expression {key_assignment!r}. "
        "A key of this length will cause Daphne to return HTTP 400 on the WebSocket upgrade."
    )


def test_ws_key_decodes_to_exactly_16_bytes():
    """Executing the deploy.yml key assignment must produce a key that decodes to 16 bytes (AC-52.2).

    RFC 6455 §4.1: the Sec-WebSocket-Key header must encode exactly 16 bytes as base64.
    The old invalid key ('c29sYW5hdHJpbGx5X2FjNDgzX2tleQ==', base64 of 'solanatrilly_ac483_key')
    decoded to 22 bytes, causing Daphne to reject every WS upgrade with HTTP 400.

    This test fails if the key is reverted to any value that does not decode to exactly 16 bytes,
    catching regressions that a 24-char length check alone would miss (e.g. a base64-padded
    value whose decoded length differs from 16).
    """
    key_assignment = _extract_key_assignment()
    assert key_assignment is not None, (
        "No 'key = ' assignment found in deploy.yml PYEOF block — cannot validate decoded length."
    )
    key = _evaluate_key_assignment(key_assignment)
    assert key is not None, (
        f"Key assignment {key_assignment!r} did not produce a 'key' variable."
    )
    try:
        decoded = base64.b64decode(key)
    except Exception as exc:
        raise AssertionError(
            f"deploy.yml WS key {key!r} (from {key_assignment!r}) is not valid base64: {exc}"
        ) from exc
    assert len(decoded) == 16, (
        f"deploy.yml WS key must decode to exactly 16 bytes (RFC 6455 §4.1); "
        f"got {len(decoded)} bytes from key {key!r} (expression: {key_assignment!r}). "
        "A key whose decoded length is not 16 bytes will cause Daphne to return HTTP 400."
    )
