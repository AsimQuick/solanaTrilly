# ---
# module: core.tests.test_logging_config_observability
# sprint: sprint-14 (cutover / live observability)
# story: firehose observability hotfix
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: django.conf.settings, logging, io
# ---
"""Guard: the app loggers must be routed to a stdout console handler.

Bug (found in firehose Window 1): config/settings.py had NO LOGGING config, so
Python's default swallowed everything below WARNING and the live firehose
daemon's [FIREHOSE] INFO observability (collection / graduation / score /
paper-trade) was invisible in `docker logs` — the daemon ran completely silent.

These tests pin the fix: core / copytrade / trading loggers emit INFO through a
StreamHandler, and a functional check proves an INFO record actually propagates
to a handler (not swallowed).
"""
import io
import logging

from django.conf import settings


def test_logging_config_routes_app_loggers_to_console():
    """settings.LOGGING wires a console StreamHandler for the app loggers."""
    cfg = settings.LOGGING
    assert isinstance(cfg, dict) and cfg.get("handlers"), "LOGGING must be configured"
    console = cfg["handlers"].get("console")
    assert console is not None, "a 'console' handler must exist"
    assert console["class"] == "logging.StreamHandler", "console must be a StreamHandler"

    for name in ("core", "copytrade", "trading"):
        lg = cfg["loggers"].get(name)
        assert lg is not None, f"logger '{name}' must be configured"
        assert "console" in lg["handlers"], f"logger '{name}' must use the console handler"
        assert lg.get("level") not in (None, "WARNING", "ERROR", "CRITICAL"), (
            f"logger '{name}' must emit at INFO/DEBUG so observability is visible"
        )


def test_core_logger_info_actually_emits():
    """A core.* INFO record reaches a handler (functional, not just config)."""
    logger = logging.getLogger("core.firehose.observability_test")
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        # The 'core' logger is configured at INFO; ensure effective level allows it.
        logger.info("[FIREHOSE] observability smoke")
        handler.flush()
    finally:
        logger.removeHandler(handler)
    assert "[FIREHOSE] observability smoke" in buf.getvalue(), (
        "core.* INFO records must not be swallowed"
    )
