# ---
# module: copytrade.validators
# sprint: sprint-12
# story: US-58 AC-58.3
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-18
# dependencies: pydantic>=2.0
# ---
"""Cohort-JSON validator for the leaderboard.json contract (SPEC §2).

Parses and validates a leaderboard.json payload against the SPEC §2 schema:
  - schema_version must be '1.0'
  - cohort_id is required
  - created_at is required
  - trade_config holds all SPEC §2 trade params with the same bounds as
    CopyTradeConfig (write-path-rejection discipline matching US-10)
  - wallets[] each require a non-empty address; all other fields informational

The public entry point is validate_cohort_json(data) which raises
CohortJsonValidationError with a clear message on any violation.
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, model_validator
from pydantic import ValidationError as PydanticValidationError

from copytrade.schemas import CohortV2

# ---------------------------------------------------------------------------
# Sub-schemas
# ---------------------------------------------------------------------------


class CohortWalletSchema(BaseModel):
    """One wallet entry in the leaderboard.json wallets[] array (SPEC §2).

    Only address is required by the engine; the other fields are informational
    metadata from the leaderboard and are surfaced on the dashboard.
    """

    address: str
    rank: Optional[int] = None
    precision: Optional[float] = None
    total_pump_buys: Optional[int] = None
    median_lead_min: Optional[float] = None

    @model_validator(mode="after")
    def check_address_non_empty(self) -> "CohortWalletSchema":
        if not self.address or not self.address.strip():
            raise ValueError("wallet address must be a non-empty string")
        return self


class CohortTradeConfigSchema(BaseModel):
    """Validated trade_config block from leaderboard.json (SPEC §2).

    Bounds are identical to CopyTradeConfig (AC-58.1) — same write-path
    rejection discipline as US-10 so an invalid JSON cannot be loaded.

    mirror_wallet_sells is a hard invariant: it MUST be False.  The engine
    exits on OUR configurable rules (TP/SL/CURVE/TIMER) and never mirrors
    the watched wallet's sells (SPEC §3).
    """

    mode: Literal["observe", "live"] = "observe"
    sol_size_per_trade: float = Field(gt=0)
    take_profit_pct: float = Field(gt=0)
    stop_loss_pct: float = Field(gt=0, le=100)
    exit_before_graduation: bool = True
    curve_completion_exit_pct: float = Field(gt=0, le=100)
    max_hold_seconds: int = Field(gt=0)
    max_concurrent_positions: int = Field(gt=0)
    copy_only_pumpfun_curve_buys: bool = True
    copy_first_buy_only: bool = True
    dedupe_token_across_wallets: bool = True
    mirror_wallet_sells: bool = False

    @model_validator(mode="after")
    def check_mirror_wallet_sells(self) -> "CohortTradeConfigSchema":
        if self.mirror_wallet_sells:
            raise ValueError(
                "mirror_wallet_sells must be False — the engine exits on OUR "
                "configurable rules (TP/SL/CURVE/TIMER), never by mirroring the "
                "watched wallet's sells (SPEC §3)."
            )
        return self


class CohortJsonSchema(BaseModel):
    """Validated schema for the leaderboard.json cohort-JSON contract (SPEC §2).

    schema_version must be the literal string '1.0'.  cohort_id and created_at
    are required strings.  trade_config is fully validated via
    CohortTradeConfigSchema.  Each wallet entry in wallets[] must have a
    non-empty address.
    """

    schema_version: Literal["1.0"]
    cohort_id: str
    created_at: str
    description: str = ""
    trade_config: CohortTradeConfigSchema
    wallets: List[CohortWalletSchema]

    @model_validator(mode="after")
    def check_cohort_id_non_empty(self) -> "CohortJsonSchema":
        if not self.cohort_id or not self.cohort_id.strip():
            raise ValueError("cohort_id must be a non-empty string")
        return self

    @model_validator(mode="after")
    def check_created_at_non_empty(self) -> "CohortJsonSchema":
        if not self.created_at or not self.created_at.strip():
            raise ValueError("created_at must be a non-empty string")
        return self


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CohortJsonValidationError(Exception):
    """Raised when a leaderboard.json payload fails schema validation.

    Carries a human-readable message identifying the failing field or constraint
    so the operator knows exactly what to fix — same discipline as US-10.
    """


def validate_cohort_json(data: dict) -> CohortJsonSchema:
    """Parse and validate a leaderboard.json payload against the SPEC §2 contract.

    Args:
        data: The parsed JSON dict from leaderboard.json.

    Returns:
        A validated CohortJsonSchema instance on success.

    Raises:
        CohortJsonValidationError: On any schema or bounds violation, with a
            clear message identifying the failing field(s).
    """
    try:
        return CohortJsonSchema.model_validate(data)
    except PydanticValidationError as exc:
        raise CohortJsonValidationError(
            f"leaderboard.json failed validation: {exc}"
        ) from exc


def validate_cohort_v2(data: dict) -> CohortV2:
    """Parse and validate a cohort.json payload against the `copytrade-2.0` contract.

    This is the REAL consumption format the engine consumes (two strategy heads,
    `global`+`strategies[]`, USD sizing, per-head exits including mirror-sell).

    Args:
        data: The parsed JSON dict from cohort.json.

    Returns:
        A validated CohortV2 instance on success.

    Raises:
        CohortJsonValidationError: On any schema or bounds violation.
    """
    try:
        return CohortV2.model_validate(data)
    except PydanticValidationError as exc:
        raise CohortJsonValidationError(
            f"cohort.json (copytrade-2.0) failed validation: {exc}"
        ) from exc


def validate_cohort_any(data: dict):
    """Validate a cohort payload, dispatching on its declared schema version.

    - `copytrade-2.0` -> CohortV2 (the live engine's contract)
    - `1.0`           -> CohortJsonSchema (legacy SPEC §2, retained for back-compat)

    Raises CohortJsonValidationError when the schema version is missing/unknown
    or validation fails.
    """
    version = data.get("schema_version")
    if version == "copytrade-2.0":
        return validate_cohort_v2(data)
    if version == "1.0":
        return validate_cohort_json(data)
    raise CohortJsonValidationError(
        f"unknown cohort schema_version {version!r}; expected 'copytrade-2.0' or '1.0'"
    )
