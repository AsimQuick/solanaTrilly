# ---
# module: trading.sender
# sprint: sprint-13, feat/copy-live-exec-curve-ix, hotfix/preflight-ghostbuy,
#   feature/copy-capital-path-wiring
# story: US-65 AC-65.3, copy-live-exec, preflight-ghostbuy, EPIC-copy-capital-path-wiring
# status: refactored
# created-by: dev-team
# last-updated: 2026-06-21
# dependencies: stdlib (collections, dataclasses, time)
# ---
"""Live RPC/Sender boundary — Cutover-gated (AC-65.3).

This module is the SOLE live-send boundary in the execution apparatus.

SAFETY CONTRACT (§16 Cutover-gated):
  - This module is NEVER imported or invoked by any test file.
  - This module is NEVER imported or invoked from any observe/paper code path.
  - ExecutionCore.execute_buy / execute_sell gate on trading_enabled=False
    and return an ObserveResult before reaching this module.
  - Real mainnet sends require Cutover (PRD §16): the operator must provision
    the trading-wallet secret AND explicitly set trading_enabled=True.

Structure ported from solanaBilly app/tasks/trading_tasks.py
(chainstacklabs manual_buy/sell_pumpswap.py port reference per AC-65.3):
  - Sender→RPC fallback (sells only — buys excluded to prevent double-submit)
  - Circuit breaker (opens after N 5xx errors in W seconds; cooldown C seconds)
  - Ghost-buy-verify (polls ATA balance < 10% of expected → no Position)
  - Anchor-decode (6002 TooMuchSolRequired, 6003 TooLittleSolReceived,
    6023 NotEnoughTokensToSell — never raises; decodes meta.err from the chain)

None of these paths execute against mainnet in any test or observe/paper run.
All HTTP calls require a live wallet key provisioned at Cutover (§16).
"""

from __future__ import annotations

import collections
import logging
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Anchor error codes (pump.fun program — verified PRs #376/#377)
# ---------------------------------------------------------------------------

PUMPSWAP_ANCHOR_ERRORS: dict[int, str] = {
    6002: "TooMuchSolRequired",    # buy-side slippage cap exceeded (PR #376)
    6003: "TooLittleSolReceived",  # sell-side slippage exceeded
    6023: "NotEnoughTokensToSell", # sell-side token count mismatch (PR #377)
}


def decode_anchor_error(err: object) -> dict:
    """Best-effort decode of a Solana tx meta.err into named Anchor fields.

    The common instruction-failure shape is:
        {"InstructionError": [<ix_index>, {"Custom": <code>}]}

    Returns a flat dict with:
      anchor_name       — human name for the error code, or None if unknown
      custom_code       — integer Anchor custom error code, or None
      instruction_index — index of the failing instruction, or None
      raw               — repr(err) for logging, always present

    Never raises — decoding must never break the confirmation path.

    Args:
        err: The meta.err value from a Solana tx result (any type).

    Returns:
        Dict with keys anchor_name, custom_code, instruction_index, raw.
    """
    out: dict = {
        "anchor_name": None,
        "custom_code": None,
        "instruction_index": None,
        "raw": repr(err),
    }
    try:
        if isinstance(err, dict) and "InstructionError" in err:
            ix = err["InstructionError"]
            if isinstance(ix, (list, tuple)) and len(ix) == 2:
                if isinstance(ix[0], int):
                    out["instruction_index"] = ix[0]
                detail = ix[1]
                if (
                    isinstance(detail, dict)
                    and "Custom" in detail
                    and isinstance(detail["Custom"], int)
                ):
                    out["custom_code"] = detail["Custom"]
                    out["anchor_name"] = PUMPSWAP_ANCHOR_ERRORS.get(detail["Custom"])
                elif isinstance(detail, str):
                    out["anchor_name"] = detail
        elif isinstance(err, str):
            out["anchor_name"] = err
    except Exception:  # noqa: BLE001 — decoding must never break the confirm path
        pass
    return out


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class GhostBuyResult:
    """Result of the ghost-buy ATA balance verification poll.

    A "ghost buy" occurs when the outer tx is confirmed on-chain but the inner
    pump.fun instruction fails (slippage exceeded, curve drained) — the tx
    lands in a block but delivers 0 tokens to the wallet.

    Attributes:
        received: True = tokens landed; False = ghost buy; None = inconclusive
                  (all poll attempts returned RPC errors, so we cannot confirm
                  whether tokens arrived — the position is NOT booked; the send
                  signature is logged for manual reconciliation if tokens landed).
        balance:  Actual ATA balance in raw token units.
                  0  → ghost buy confirmed.
                  -1 → inconclusive (all RPC errors).
                  >0 → tokens received.
    """

    received: bool | None
    balance: int


@dataclass
class SimulateResult:
    """Result of a simulateTransaction preflight check.

    Used by Sender.simulate() and consumed by ExecutionCore.execute_buy() to
    gate real sends.  Never raises — network / decode errors produce ok=None
    (treated as inconclusive → do NOT send).

    Attributes:
        ok:     True  → simulation succeeded (no err in result.value.err).
                False → simulation failed (anchor / program error present).
                None  → inconclusive (network error, malformed response) —
                        the caller must treat this as fail-safe and NOT send.
        err:    The decoded anchor error dict when ok=False.  None when ok=True
                or inconclusive.
        raw_err: The raw result.value.err object from the RPC response, or None.
    """

    ok: bool | None
    err: dict | None = None
    raw_err: object = None


@dataclass
class SendResult:
    """Outcome of a submitted Solana transaction.

    Attributes:
        signature:  Transaction signature (base58 string).
        confirmed:  Whether the tx was confirmed on-chain via getTransaction
                    or getSignatureStatuses.
        synthetic:  True if confirmation was via getSignatureStatuses fallback
                    (tx on chain but getTransaction timed out — still actionable).
        meta_err:   Non-null when the tx was included in a block but the inner
                    instruction execution failed (slippage exceeded, curve
                    drained, etc.).
        anchor_err: Decoded meta_err dict (always present; fields are None when
                    meta_err is None or un-decodable).
        pre_balance_lamports:  Payer's SOL lamport balance BEFORE the tx
                               (index 0 of meta.preBalances).  None if not
                               available from the confirmation response.
        post_balance_lamports: Payer's SOL lamport balance AFTER the tx
                               (index 0 of meta.postBalances).  None if not
                               available.
        sol_spent_lamports:    pre - post (total SOL debit including tx fee
                               and any ATA-creation rent).  None if either
                               balance is unavailable.
                               Approximation: fee + rent are bundled; we
                               cannot isolate token-purchase SOL without
                               parsing inner instructions.
    """

    signature: str
    confirmed: bool
    synthetic: bool = False
    meta_err: object = None
    anchor_err: dict = field(default_factory=lambda: {
        "anchor_name": None,
        "custom_code": None,
        "instruction_index": None,
        "raw": "None",
    })
    pre_balance_lamports: int | None = None
    post_balance_lamports: int | None = None
    sol_spent_lamports: int | None = None


# ---------------------------------------------------------------------------
# Sender configuration
# ---------------------------------------------------------------------------


@dataclass
class SenderConfig:
    """Configuration for the Sender — read from env at Cutover (§16).

    All values are config-driven per Principle #1 — never literals in code.

    Attributes:
        sender_url:             Helius Sender /fast endpoint URL.
        rpc_url:                Fallback plain Helius RPC URL.
        rpc_fallback_on_5xx:    Enable Sender 5xx → RPC fallback for sells.
        cb_threshold:           # of 5xx errors in cb_window_s to open breaker.
        cb_window_s:            Sliding window for counting Sender 5xx errors.
        cb_cooldown_s:          Duration the breaker stays open after tripping.
        confirm_retries:        Number of getTransaction poll attempts.
        ghost_buy_retries:      Number of ATA balance poll attempts.
        ghost_buy_backoff_ms:   Per-attempt sleep schedule in milliseconds.
    """

    sender_url: str = "https://sender.helius-rpc.com/fast"
    rpc_url: str = ""
    rpc_fallback_on_5xx: bool = True
    cb_threshold: int = 5
    cb_window_s: int = 60
    cb_cooldown_s: int = 120
    confirm_retries: int = 30
    ghost_buy_retries: int = 4
    ghost_buy_backoff_ms: list = field(
        default_factory=lambda: [500, 1000, 2000, 3000]
    )


# ---------------------------------------------------------------------------
# Circuit breaker (tracks Sender 5xx in a sliding window — no Redis needed)
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Sliding-window circuit breaker for Helius Sender 5xx outages.

    Tracks Sender HTTP 5xx timestamps in an in-process deque. When
    >= threshold 5xx errors occur within window_s, the circuit opens and
    all sell transactions skip the Sender endpoint for cooldown_s seconds.

    Sells only — buys are excluded from the fallback path because accidentally
    double-submitting a buy (once via Sender, once via RPC) would open two
    positions.  The circuit breaker is a sell-only mechanism.

    State is per-process: a worker restart resets the counter — acceptable
    because a real outage will trip the breaker again quickly.
    """

    def __init__(self, threshold: int = 5, window_s: int = 60, cooldown_s: int = 120) -> None:
        self._threshold = threshold
        self._window_s = window_s
        self._cooldown_s = cooldown_s
        self._timestamps: collections.deque = collections.deque()
        self._open_until: float = 0.0

    def is_open(self) -> bool:
        """Return True when the circuit is open (route sells to RPC directly)."""
        now = time.monotonic()
        if self._open_until > now:
            return True
        # Prune timestamps outside the sliding window
        cutoff = now - self._window_s
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
        if len(self._timestamps) >= self._threshold:
            self._open_until = now + self._cooldown_s
            return True
        return False

    def record_5xx(self) -> None:
        """Record a Sender HTTP 5xx error in the sliding window."""
        self._timestamps.append(time.monotonic())

    def reset(self) -> None:
        """Reset the breaker (used in tests / after operator cooldown decision)."""
        self._timestamps.clear()
        self._open_until = 0.0

    @property
    def error_count(self) -> int:
        """Current number of 5xx errors in the active window."""
        now = time.monotonic()
        cutoff = now - self._window_s
        return sum(1 for ts in self._timestamps if ts >= cutoff)


# ---------------------------------------------------------------------------
# Sender — live RPC/Sender boundary (Cutover-gated)
# ---------------------------------------------------------------------------


class Sender:
    """Live RPC/Sender boundary for PumpSwap buy/sell transactions.

    This class performs actual HTTP calls to Solana mainnet via the Helius
    Sender /fast endpoint.  It is NEVER instantiated in observe/paper mode —
    ExecutionCore gates access behind trading_enabled=True (Cutover, §16).

    Policy summary (ported from solanaBilly trading_tasks.py §14):

    BUY:
      - Route via Helius Sender (maxRetries=0 — Sender manages retries).
      - No RPC fallback: a double-submitted buy opens two positions.
      - Follow with ghost-buy ATA balance verification (poll up to N times).
      - If < 10% of expected tokens received → ghost buy → no Position.

    SELL (4 attempts, gaps [0,2,8,30]s normal / [0,2,2,2]s PANIC):
      - Per-attempt Sender 5xx → plain RPC fallback (rpc_fallback_on_5xx).
      - Circuit breaker: >= threshold 5xx in window_s → open for cooldown_s.
      - While breaker is open, all sells skip Sender and route to plain RPC.
      - Slippage tiers per attempt: TIGHT 800 / NORMAL 1500 / LOSS 2500 /
        PANIC [5000,7000,9000,9900] bps.

    CONFIRMATION (shared):
      - Poll getTransaction up to confirm_retries times.
      - Phase 3: check meta.err on each confirmed tx; non-null → reverted.
      - Fallback: getSignatureStatuses with searchTransactionHistory=true
        returns a synthetic result so Jito-routed txs landed after the
        window are not orphaned.

    ANCHOR DECODE (shared):
      - decode_anchor_error() decodes meta.err into anchor_name/custom_code.
      - Never raises — decoding must not break the confirmation path.
    """

    def __init__(self, config: SenderConfig | None = None, wallet_pubkey: str = "") -> None:
        """Initialise the Sender with config and the trading wallet's pubkey.

        Args:
            config:        SenderConfig instance, or None for defaults.
            wallet_pubkey: Base58 pubkey of the live trading wallet.  Required for
                           verify_ghost_buy to query the real ATA balance — without
                           it _get_ata_balance uses an empty owner → returns 0 →
                           every buy is misclassified as a ghost buy (F2 fix).
                           Injected from trading.tx_signer.wallet_pubkey_str(keypair)
                           at Cutover (run_copytrade_engine.py live path).
                           Empty string is safe in observe/paper mode: trading_enabled
                           gates are False so send_buy/verify_ghost_buy are never called.
        """
        self._cfg = config or SenderConfig()
        # F2 fix: store wallet_pubkey on the Sender at construction so that
        # verify_ghost_buy → _get_ata_balance queries the REAL wallet's ATA,
        # not the empty-string placeholder that caused every buy to read balance 0.
        self._wallet_pubkey: str = wallet_pubkey or ""
        self._circuit_breaker = CircuitBreaker(
            threshold=self._cfg.cb_threshold,
            window_s=self._cfg.cb_window_s,
            cooldown_s=self._cfg.cb_cooldown_s,
        )
        # Fail-LOUD on misconfiguration: simulate() and _confirm() both POST to
        # rpc_url. If it is empty while the Sender is otherwise live-configured,
        # every simulate() raises -> SimulateResult(ok=None) -> execute_buy
        # refuses every buy with reason="preflight_failed" SILENTLY (no crash,
        # just a WARNING per tx). Surface it once at construction instead.
        if self._cfg.sender_url and not self._cfg.rpc_url:
            logging.getLogger("trading").error(
                "[sender] rpc_url is EMPTY — simulate()/_confirm() will fail and "
                "ALL live sends will be refused (preflight_failed). Set the Helius "
                "RPC URL in SenderConfig.rpc_url before enabling trading."
            )
        # F2 fix (continued): warn loudly if wallet_pubkey is absent in live mode
        # so operators know ghost-buy queries will be degraded (won't reach real
        # mainnet at all unless sender_url is set, but emit the warning anyway for
        # any future code path that constructs a Sender with an empty pubkey).
        if self._cfg.sender_url and not self._wallet_pubkey:
            logging.getLogger("trading").warning(
                "[sender] wallet_pubkey is EMPTY — verify_ghost_buy will query "
                "getTokenAccountsByOwner with an empty owner, returning balance=0 "
                "and classifying every buy as a ghost buy. Inject the real wallet "
                "pubkey at Cutover via Sender(config, wallet_pubkey=pubkey_str)."
            )

    # ------------------------------------------------------------------
    # Public API — called ONLY from ExecutionCore when trading_enabled=True
    # ------------------------------------------------------------------

    def simulate(self, serialized_tx_b64: str) -> SimulateResult:
        """POST simulateTransaction and return whether it would succeed.

        Uses replaceRecentBlockhash=True so the simulation is not invalidated
        by an expired blockhash.  sigVerify=False avoids the need to re-sign
        for simulation.

        Returns:
            SimulateResult(ok=True)   — simulation succeeded (no err).
            SimulateResult(ok=False)  — simulation failed (program error / slippage).
            SimulateResult(ok=None)   — inconclusive (network error, unexpected
                                        response shape).  Caller must treat as
                                        fail-safe (do NOT send).

        Never raises — all exceptions are caught and mapped to ok=None.
        """
        try:
            import requests  # deferred import — live path only

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "simulateTransaction",
                "params": [
                    serialized_tx_b64,
                    {
                        "encoding": "base64",
                        "sigVerify": False,
                        "replaceRecentBlockhash": True,
                    },
                ],
            }
            try:
                resp = requests.post(self._cfg.rpc_url, json=payload, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                import logging as _logging
                _logging.getLogger("trading").warning(
                    "[sender] simulate network-error: %s — inconclusive", exc
                )
                return SimulateResult(ok=None)

            if "error" in data:
                import logging as _logging
                _logging.getLogger("trading").warning(
                    "[sender] simulate rpc-error: %s — inconclusive", data["error"]
                )
                return SimulateResult(ok=None)

            value = (data.get("result") or {}).get("value")
            if value is None:
                import logging as _logging
                _logging.getLogger("trading").warning(
                    "[sender] simulate missing result.value — inconclusive"
                )
                return SimulateResult(ok=None)

            raw_err = value.get("err")
            if raw_err is None:
                return SimulateResult(ok=True)

            decoded = decode_anchor_error(raw_err)
            return SimulateResult(ok=False, err=decoded, raw_err=raw_err)

        except Exception as exc:  # noqa: BLE001 — simulate must never raise
            import logging as _logging
            _logging.getLogger("trading").warning(
                "[sender] simulate unexpected-error: %s — inconclusive", exc
            )
            return SimulateResult(ok=None)

    def send_buy(self, serialized_tx_b64: str) -> SendResult:
        """Submit a buy transaction via Helius Sender.

        No RPC fallback — a double-submitted buy opens two positions.
        Always follow with verify_ghost_buy() to confirm token receipt.

        Args:
            serialized_tx_b64: Base64-encoded signed transaction.

        Returns:
            SendResult with confirmed=True and meta_err checked.

        Raises:
            RuntimeError: On Sender/RPC error or missing tx signature.
        """
        url = self._cfg.sender_url or self._cfg.rpc_url
        sig = self._submit(url, serialized_tx_b64, max_retries_zero=True)
        return self._confirm(sig)

    def send_sell(self, serialized_tx_b64: str) -> SendResult:
        """Submit a sell transaction with Sender→RPC fallback and circuit breaker.

        Per-attempt policy:
          1. If circuit breaker is open → route to plain RPC directly.
          2. Else try Helius Sender.
          3. On Sender 5xx → record error, open breaker if threshold, fall
             back to plain RPC (when rpc_fallback_on_5xx=True).
          4. Confirmation via _confirm() with meta.err check.

        Args:
            serialized_tx_b64: Base64-encoded signed transaction.

        Returns:
            SendResult with confirmed status and anchor error info.

        Raises:
            RuntimeError: On all-path submission failure.
        """
        use_rpc_directly = self._circuit_breaker.is_open()
        if use_rpc_directly:
            url = self._cfg.rpc_url
            sig = self._submit(url, serialized_tx_b64, max_retries_zero=False)
        else:
            try:
                sig = self._submit(
                    self._cfg.sender_url,
                    serialized_tx_b64,
                    max_retries_zero=True,
                )
            except _SenderHttp5xxError as exc:
                self._circuit_breaker.record_5xx()
                if not self._cfg.rpc_fallback_on_5xx:
                    raise RuntimeError(f"Sender 5xx and rpc_fallback_on_5xx=False: {exc}") from exc
                # Immediate RPC fallback (same signed tx, same nonce)
                sig = self._submit(
                    self._cfg.rpc_url,
                    serialized_tx_b64,
                    max_retries_zero=False,
                )

        return self._confirm(sig)

    def verify_ghost_buy(
        self,
        mint_address: str,
        expected_tokens: int,
        get_balance_fn: object = None,
    ) -> GhostBuyResult:
        """Poll ATA balance to verify a buy actually delivered tokens.

        After a buy tx is confirmed, the inner PumpSwap instruction may still
        have failed silently (slippage exceeded, curve drained) — the outer
        tx is on-chain but the wallet receives 0 tokens (the 'ghost buy').

        Polls the wallet's ATA balance up to ghost_buy_retries times.
        Returns (True, balance) when balance > 10% of expected_tokens.
        Returns (False, 0) when confirmed 0-balance ghost buy.
        Returns (None, -1) when all RPC calls failed (inconclusive).

        In live mode, get_balance_fn must be a callable(mint_address) → int|None.
        The live impl queries getTokenAccountsByOwner via the Helius RPC.

        Args:
            mint_address:    Token mint to check.
            expected_tokens: Estimated token amount from the buy quote.
            get_balance_fn:  Optional callable override for testing (Cutover: live query).

        Returns:
            GhostBuyResult(received=True|False|None, balance=int).
        """
        min_meaningful = max(1, int(expected_tokens * 0.10)) if expected_tokens > 0 else 1
        had_zero = False

        for attempt in range(self._cfg.ghost_buy_retries):
            if attempt > 0:
                idx = min(attempt - 1, len(self._cfg.ghost_buy_backoff_ms) - 1)
                time.sleep(self._cfg.ghost_buy_backoff_ms[idx] / 1000.0)

            if get_balance_fn is not None:
                balance = get_balance_fn(mint_address)
            else:
                # F2 fix: pass self._wallet_pubkey (injected at construction from the
                # real keypair) so getTokenAccountsByOwner queries the correct owner.
                # The previous code called _get_ata_balance with the default "" owner,
                # which always returned 0 tokens → every buy was misclassified as a
                # ghost buy → no position ever written in live mode (SHOWSTOPPER).
                balance = self._get_ata_balance(mint_address, self._wallet_pubkey)

            if balance is None:
                continue  # RPC error — inconclusive, keep retrying

            if balance <= 0:
                had_zero = True
                continue

            if balance < min_meaningful:
                return GhostBuyResult(received=False, balance=balance)

            return GhostBuyResult(received=True, balance=balance)

        if had_zero:
            return GhostBuyResult(received=False, balance=0)
        return GhostBuyResult(received=None, balance=-1)

    # ------------------------------------------------------------------
    # Internal helpers — LIVE, Cutover-only
    # ------------------------------------------------------------------

    def _submit(self, url: str, serialized_tx_b64: str, max_retries_zero: bool) -> str:
        """POST sendTransaction to url; return tx signature or raise.

        Raises _SenderHttp5xxError on HTTP 5xx (lets send_sell catch and
        fall back to RPC); raises RuntimeError on other failures.
        """
        import requests  # deferred import — not available in observe/paper env

        options: dict = {"encoding": "base64", "skipPreflight": True}
        if max_retries_zero:
            options["maxRetries"] = 0

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "sendTransaction",
            "params": [serialized_tx_b64, options],
        }
        try:
            resp = requests.post(url, json=payload, timeout=15)
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(f"sendTransaction connection error: {exc}") from exc

        if resp.status_code >= 500:
            raise _SenderHttp5xxError(
                f"Sender HTTP {resp.status_code}: {resp.text[:200]}"
            )
        resp.raise_for_status()

        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"sendTransaction RPC error: {data['error']}")
        sig = data.get("result")
        if not sig:
            raise RuntimeError(f"sendTransaction returned no signature: {data}")
        return sig

    def _confirm(self, sig: str) -> SendResult:
        """Poll getTransaction until confirmed; check meta.err; fall back to
        getSignatureStatuses if the tx is on-chain but slow to surface.

        Returns a SendResult — synthetic=True when fallback was used.
        """
        import requests  # deferred import — live path only

        url = self._cfg.rpc_url

        for _ in range(self._cfg.confirm_retries):
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [sig, {"encoding": "json", "maxSupportedTransactionVersion": 0}],
            }
            try:
                resp = requests.post(url, json=payload, timeout=10)
                resp.raise_for_status()
                tx = resp.json().get("result")
            except Exception:
                time.sleep(2)
                continue

            if tx is None:
                time.sleep(2)
                continue

            meta = tx.get("meta") or {}
            meta_err = meta.get("err")
            anchor = decode_anchor_error(meta_err) if meta_err is not None else {
                "anchor_name": None,
                "custom_code": None,
                "instruction_index": None,
                "raw": "None",
            }

            # Extract payer SOL balance delta (index 0 = fee-payer account).
            # pre/postBalances are lamport arrays parallel to tx.message.accountKeys.
            # The total debit (pre[0] - post[0]) covers: tx fee + ATA-creation rent
            # (if any) + SOL spent on the instruction (token purchase cost).
            # Approximation: fee and rent are bundled with the purchase cost;
            # isolating the pure token-purchase SOL would require parsing inner
            # instructions (out of scope — documented in PR body).
            pre_bals = meta.get("preBalances") or []
            post_bals = meta.get("postBalances") or []
            pre_lam: int | None = None
            post_lam: int | None = None
            sol_spent_lam: int | None = None
            if pre_bals and post_bals:
                try:
                    pre_lam = int(pre_bals[0])
                    post_lam = int(post_bals[0])
                    delta = pre_lam - post_lam
                    sol_spent_lam = delta if delta >= 0 else None
                except (TypeError, ValueError, IndexError):
                    pass

            return SendResult(
                signature=sig,
                confirmed=meta_err is None,
                synthetic=False,
                meta_err=meta_err,
                anchor_err=anchor,
                pre_balance_lamports=pre_lam,
                post_balance_lamports=post_lam,
                sol_spent_lamports=sol_spent_lam,
            )

        # Fallback: getSignatureStatuses (handles Jito txs that land late)
        status = self._get_signature_status(sig)
        if status is not None:
            return SendResult(
                signature=sig,
                confirmed=status.get("err") is None,
                synthetic=True,
                meta_err=status.get("err"),
                anchor_err=decode_anchor_error(status.get("err")),
            )

        # Timeout — tx never confirmed within retry window
        return SendResult(signature=sig, confirmed=False, synthetic=False)

    def _get_signature_status(self, sig: str) -> dict | None:
        """Query getSignatureStatuses with searchTransactionHistory=true."""
        import requests  # deferred import — live path only

        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getSignatureStatuses",
            "params": [[sig], {"searchTransactionHistory": True}],
        }
        try:
            resp = requests.post(self._cfg.rpc_url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            values = (data.get("result") or {}).get("value") or [None]
            return values[0]
        except Exception:
            return None

    def _get_ata_balance(self, mint_address: str, wallet_pubkey: str = "") -> int | None:
        """Query ATA token balance for the trading wallet.  Returns None on error.

        Args:
            mint_address:   Token mint to query.
            wallet_pubkey:  Base58 pubkey of the wallet owner.  Injected on the
                            live path via trading.tx_signer.wallet_pubkey_str(keypair)
                            so ghost-buy verification queries the real owner's ATA
                            (not the placeholder empty string that was here before).
                            Empty string is still accepted for backwards compatibility
                            with existing test mocks — but the live Sender call site
                            MUST supply the real pubkey.
        """
        import requests  # deferred import — live path only

        url = self._cfg.rpc_url
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getTokenAccountsByOwner",
            "params": [
                wallet_pubkey,  # real wallet pubkey (injected from tx_signer at Cutover)
                {"mint": mint_address},
                {"encoding": "jsonParsed"},
            ],
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            accounts = (data.get("result") or {}).get("value") or []
            if not accounts:
                return 0
            info = (accounts[0].get("account") or {}).get("data", {}).get("parsed", {}).get("info", {})
            amount = info.get("tokenAmount", {}).get("amount")
            return int(amount) if amount is not None else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Internal exception — not part of the public API
# ---------------------------------------------------------------------------


class _SenderHttp5xxError(RuntimeError):
    """Raised internally when the Helius Sender returns HTTP 5xx.

    Caught by send_sell to trigger the RPC fallback path.
    Not raised from send_buy (buys do not fall back to avoid double-submit).
    """
