# ---
# module: core.schemas
# sprint: sprint-3, sprint-8, sprint-10
# story: US-10 AC-10.1, AC-10.2, AC-10.3, AC-10.4; US-35 AC-35.1; US-38 AC-38.1, AC-38.2; US-48 AC-48.2; US-49 AC-49.1
# status: implemented
# created-by: dev-team
# last-updated: 2026-06-17
# dependencies: pydantic>=2.0
# ---
"""Pydantic v2 schema for the PipelineConfig tunable sections (PRD §5.1, §5.2).

Each section model mirrors the corresponding JSONField in the PipelineConfig
Django model.  The top-level PipelineConfigSchema validates a complete config
and enforces the §5.2 cross-section invariants at construction time, acting as
the save-time gate for both the admin UI and the model write path (AC-10.4).

Section hierarchy:
    PipelineConfigSchema
    ├── detection: DetectionConfig
    │   └── filter: DetectionFilter
    ├── tape:     TapeConfig
    ├── scoring:  ScoringConfig
    ├── outcome:  OutcomeConfig
    └── trading:  TradingConfig
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class DetectionFilter(BaseModel):
    """Sub-schema for the Birdeye SUBSCRIBE_MEME filter parameters."""

    source: str = "pump_dot_fun"
    graduated: bool = True


class DetectionConfig(BaseModel):
    """Detection section: event subscription source, pre-staging, and deduplication."""

    source: str = "birdeye_meme"
    filter: DetectionFilter = Field(default_factory=DetectionFilter)
    prestage_progress_pct: float = Field(default=95.0, ge=0.0, le=100.0)
    dedupe_window_s: int = Field(default=60, gt=0)
    reconciler: str = "helius_migrate"


class TapeConfig(BaseModel):
    """Tape recorder section: AMM programs, TTL, and Birdeye polling rate.

    Two-tier idle policy (US-35 AC-35.1, oracle §2, PRD §5.2/D4):
      pre_grad_idle_kill_ttl_s — aggressive kill for UNgraduated tokens (~300 s
          default); exempt from the ≥ outcome.window_s floor because pre-grad
          tokens carry no label obligation.  The recorder snaps to the protected
          TTL at the graduation instant.
      idle_kill_ttl_s — protected post-grad TTL; MUST stay ≥ outcome.window_s
          (the D4 'never truncate a label' invariant, enforced in the cross-
          section validator).
    """

    amm_programs: list[str] = Field(
        default_factory=lambda: ["pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"]
    )
    pre_grad_idle_kill_ttl_s: int = Field(default=300, gt=0)
    idle_kill_ttl_s: int = Field(gt=0)
    reattach: bool = True
    birdeye_interval_s: int = Field(default=15, gt=0)
    # US-38 AC-38.1: ship window — how many trailing days to ship per beat run (≤7)
    lake_ship_window_days: int = Field(default=1, gt=0, le=7)
    # US-38 AC-38.2: retention window — partitions older than this many days are expired (≤7)
    lake_retention_days: int = Field(default=7, gt=0, le=7)


class ScoringConfig(BaseModel):
    """Scoring section: when and how to score a graduated token (PRD §7)."""

    score_at_elapsed_s: int = Field(gt=0)
    window_s: int = Field(gt=0)
    # §5.2 invariant: capture_buffer_s >= 3 (enforced here + in cross-section validator)
    capture_buffer_s: int = Field(default=4, ge=3)
    gate: Literal["adaptive_topk"] = "adaptive_topk"
    # AC-43.2: path to the frozen reference distribution JSON file used for
    # live single-token percentile ranking (resolves the cutover risk: no same-day
    # pool at graduation).  None means pool-based scoring only (no live single-token
    # path).  Principle #1 — the path is config-driven, never hard-coded in scorer.py.
    reference_dist_path: Optional[str] = None


class OutcomeConfig(BaseModel):
    """Outcome / labelling section: time horizon for P&L label (PRD §7.3, §8)."""

    window_s: int = Field(gt=0)
    label_def: dict = Field(default_factory=dict)


class DashboardConfig(BaseModel):
    """Dashboard realtime WebSocket section (PRD §13.4, AC-48.2, AC-49.1).

    All names and cadence are Principle #1 config-driven — no literals anywhere
    in the consumer code.  The consumer reads this section via get_active_config()
    and NEVER hard-codes channel prefixes, topic names, or candle intervals.

    candle_intervals_s (AC-49.1): the four supported tape→candle API intervals
    (1s/5s/15s/1m) served by the candle endpoint.  Config-driven so the operator
    can tune the set without touching code (Principle #1).
    """

    ws_channel_prefix: str = "tape"
    candle_topic: str = "candle_delta"
    position_topic: str = "position_delta"
    candle_interval_s: int = Field(default=15, gt=0)
    candle_intervals_s: list[int] = Field(default_factory=lambda: [1, 5, 15, 60])


class TradingConfig(BaseModel):
    """Trading section: position sizing, gate, and risk controls (PRD §10)."""

    gate: Literal["adaptive_topk"] = "adaptive_topk"
    enabled: bool = False
    position_size_sol: float = Field(default=0.1, gt=0)
    max_open_positions: int = Field(default=3, gt=0)
    slippage_bps: int = Field(default=50, ge=0)
    paper_size_usd: Optional[float] = None
    exit_policy: Optional[dict] = None


class PipelineConfigSchema(BaseModel):
    """Top-level schema for all PipelineConfig tunable sections (PRD §5.1).

    Validates a complete config and enforces the §5.2 cross-section invariants
    at construction time.  Raised ValueError wraps as Pydantic ValidationError.

    Feature-contract checking (AC-10.3) is handled by validate_feature_contract()
    and is wired to become a live DB gate when FeatureSet / ModelRegistry FKs land
    (P5/P7).  The guard runs trivially (passes) when no contract is provided.
    """

    detection: DetectionConfig = Field(default_factory=DetectionConfig)
    tape: TapeConfig
    scoring: ScoringConfig
    outcome: OutcomeConfig
    trading: TradingConfig
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

    # Optional feature-contract fields (FK stubs — populated in P5/P7).
    # When provided, validate_feature_contract() enforces the subset invariant.
    feature_contract: Optional[list[str]] = None
    feature_set_columns: Optional[list[str]] = None
    live_servable: Optional[list[str]] = None

    @model_validator(mode="after")
    def check_cross_section_invariants(self) -> "PipelineConfigSchema":
        """Enforce §5.2 cross-section invariants (AC-10.2)."""
        # Leak guard: the scoring window must close AFTER the score point
        if self.scoring.window_s <= self.scoring.score_at_elapsed_s:
            raise ValueError(
                f"scoring.window_s ({self.scoring.window_s}) must be greater than "
                f"scoring.score_at_elapsed_s ({self.scoring.score_at_elapsed_s}): "
                "the feature window must close after the score point (leak guard, PRD §5.2)"
            )
        # Label-truncation guard (D4): tape TTL must cover the full outcome window
        if self.tape.idle_kill_ttl_s < self.outcome.window_s:
            raise ValueError(
                f"tape.idle_kill_ttl_s ({self.tape.idle_kill_ttl_s}) must be >= "
                f"outcome.window_s ({self.outcome.window_s}): "
                "tape must stay live long enough to label every outcome (D4, PRD §5.2)"
            )
        # Feature-contract subset invariant (AC-10.3)
        if self.feature_contract is not None:
            self._check_feature_contract_subset()
        return self

    def _check_feature_contract_subset(self) -> None:
        """Reject a config whose feature_contract includes non-live-servable columns (AC-10.3).

        Guards now, becomes a live DB gate when FeatureSet/ModelRegistry FKs arrive (P5/P7).
        The 'guard now, regression-gate later' pattern mirrors US-2.
        """
        contract = set(self.feature_contract or [])
        if self.feature_set_columns is not None:
            columns = set(self.feature_set_columns)
            outside_columns = contract - columns
            if outside_columns:
                raise ValueError(
                    f"feature_contract contains columns not in feature_set.columns: "
                    f"{sorted(outside_columns)} (D2, PRD §5.2)"
                )
        if self.live_servable is not None:
            servable = set(self.live_servable)
            not_servable = contract - servable
            if not_servable:
                raise ValueError(
                    f"feature_contract contains non-live-servable columns: "
                    f"{sorted(not_servable)} — a training_only feature cannot be in the "
                    f"live contract (D2, PRD §5.2)"
                )

    @classmethod
    def from_model_sections(
        cls,
        detection: dict,
        tape: dict,
        scoring: dict,
        outcome: dict,
        trading: dict,
        feature_contract: Optional[list] = None,
        feature_set_columns: Optional[list] = None,
        live_servable: Optional[list] = None,
    ) -> "PipelineConfigSchema":
        """Construct from the five JSONField section dicts of a PipelineConfig model row."""
        return cls(
            detection=detection,
            tape=tape,
            scoring=scoring,
            outcome=outcome,
            trading=trading,
            feature_contract=feature_contract,
            feature_set_columns=feature_set_columns,
            live_servable=live_servable,
        )

    def to_model_sections(self) -> dict:
        """Export as section dicts suitable for storing in PipelineConfig JSONFields.

        Returns a dict with exactly the five section keys.  The feature-contract
        fields are not included (they are FK-derived, not stored in the sections).
        """
        data = self.model_dump()
        return {
            "detection": data["detection"],
            "tape": data["tape"],
            "scoring": data["scoring"],
            "outcome": data["outcome"],
            "trading": data["trading"],
        }
