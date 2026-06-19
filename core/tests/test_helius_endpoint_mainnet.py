# ---
# module: core.tests.test_helius_endpoint_mainnet
# sprint: sprint-14 (cutover / live observability)
# story: helius birth-tape endpoint hotfix
# status: implemented
# created-by: operator
# last-updated: 2026-06-19
# dependencies: core.tape.helius_birth_tape_source
# ---
"""Guard: the Helius birth-tape source must use the WORKING mainnet endpoint.

Live-window finding: HeliusBirthTapeSource pointed at `wss://atlas-mainnet.helius-rpc.com`
(Helius Atlas/Geyser enhanced WS). Under this account's plan that endpoint
connected but streamed ZERO `transactionNotification` frames, so the pre-grad
birth-tape buffer never filled and the model never scored. The working
reference (solanaBilly app/services/helius_listener.py) drives the SAME
`transactionSubscribe` against the STANDARD `wss://mainnet.helius-rpc.com/`
endpoint. This pins the source to that endpoint so it can't silently regress.
"""
from core.tape.helius_birth_tape_source import HELIUS_WS_URL, HeliusBirthTapeSource


def test_helius_ws_url_is_standard_mainnet():
    """The birth-tape endpoint must be standard mainnet, not atlas-mainnet."""
    assert HELIUS_WS_URL == "wss://mainnet.helius-rpc.com", HELIUS_WS_URL
    assert "atlas-mainnet" not in HELIUS_WS_URL, (
        "atlas-mainnet streamed zero frames under this plan — use mainnet "
        "(the solanaBilly working reference)"
    )


def test_source_defaults_to_mainnet_endpoint():
    """A default-constructed source uses the mainnet endpoint."""
    src = HeliusBirthTapeSource(api_key="dummy")
    assert getattr(src, "_endpoint") == "wss://mainnet.helius-rpc.com"
