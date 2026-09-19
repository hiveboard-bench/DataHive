"""Pydantic v2 schema for episode headers and trial annotations."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


EPISODE_SCHEMA_V1 = "datahive_episode_v1"
EPISODE_SCHEMA_CURRENT = EPISODE_SCHEMA_V1
EPISODE_SCHEMA_LEGACY = "datahive_episode_v0"
KNOWN_EPISODE_SCHEMAS = frozenset({EPISODE_SCHEMA_LEGACY, EPISODE_SCHEMA_V1})

ANNOTATION_SCHEMA_V1 = "datahive_trial_v1"
ANNOTATION_SCHEMA_CURRENT = ANNOTATION_SCHEMA_V1
ANNOTATION_SCHEMA_LEGACY = "datahive_trial_v0"
KNOWN_ANNOTATION_SCHEMAS = frozenset({ANNOTATION_SCHEMA_LEGACY, ANNOTATION_SCHEMA_V1})


class Outcome(StrEnum):
    success = "success"
    fail = "fail"
    timeout = "timeout"
    safety_stop = "safety_stop"


class FailureCause(StrEnum):
    grasp_geometry = "grasp_geometry"
    kinematic_limit = "kinematic_limit"
    perception = "perception"
    slip = "slip"
    force_limit = "force_limit"
    control_precision = "control_precision"
    other = "other"


class Strategy(StrEnum):
    prehensile = "prehensile"
    non_prehensile = "non_prehensile"


class FailureSeverity(StrEnum):
    minor = "minor"
    moderate = "moderate"
    critical = "critical"


TRIAL_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "lab_id",
    "platform_id",
    "attachment_id",
    "date",
    "operator_name",
    "outcome",
    "failure_cause",
    "failure_cause_detail",
    "severity",
    "completion_time_s",
    "completion_source",
    "n_attempts",
    "n_regrasps",
    "stage_reached",
    "strategy",
    "annotator_name",
    "notes",
    "annotated_at",
    "uploaded_at",
    "schema_version",
)


class TrialAnnotation(BaseModel):
    """One row of trials.csv. Cross-field rules are enforced in
    model_validator below. The `composed_assembly` context flag (passed via
    `TrialAnnotation.model_validate(data, context={"composed_assembly": bool})`)
    controls whether stage_reached is required or forbidden; when the
    attachment is unknown to the registry the caller omits the context key
    entirely and the stage_reached check is skipped (a warning is surfaced
    by validate.py instead of a hard failure)."""

    model_config = {"use_enum_values": True}

    trial_id: str
    lab_id: str
    platform_id: str
    attachment_id: str
    date: date
    operator_name: str = ""
    outcome: Outcome
    failure_cause: Optional[FailureCause] = None
    failure_cause_detail: str = ""
    severity: Optional[FailureSeverity] = None
    completion_time_s: Optional[float] = Field(default=None, gt=0)
    completion_source: Optional[str] = None  # timer | hdf5 | video
    n_attempts: Optional[int] = Field(default=None, ge=0)
    n_regrasps: Optional[int] = Field(default=None, ge=0)
    stage_reached: Optional[int] = Field(default=None, ge=0)
    strategy: Strategy
    annotator_name: str = ""
    notes: str = ""
    annotated_at: Optional[datetime] = None
    uploaded_at: Optional[datetime] = None
    schema_version: str = ANNOTATION_SCHEMA_CURRENT

    @model_validator(mode="after")
    def _cross_field_rules(self, info):
        if self.completion_source not in (None, "timer", "hdf5", "video"):
            raise ValueError("completion_source must be one of timer, hdf5, video")
        if self.schema_version not in KNOWN_ANNOTATION_SCHEMAS:
            raise ValueError(
                f"unsupported annotation schema_version '{self.schema_version}' "
                f"(this DataHive understands {sorted(KNOWN_ANNOTATION_SCHEMAS)}); "
                "upgrade datahive-tools"
            )
        outcome = self.outcome
        if outcome != Outcome.success:
            if not self.failure_cause:
                raise ValueError(
                    f"failure_cause is required when outcome='{outcome}' (only "
                    "'success' may omit it)"
                )
            if self.completion_time_s is not None:
                raise ValueError(
                    "completion_time_s must be blank unless outcome='success'"
                )
            if self.failure_cause == FailureCause.other and not self.failure_cause_detail.strip():
                raise ValueError(
                    "failure_cause_detail is required when failure_cause='other' "
                    "(write what actually happened)"
                )
        else:
            if self.failure_cause:
                raise ValueError("failure_cause must be blank when outcome='success'")
            if self.completion_time_s is None:
                raise ValueError("completion_time_s is required when outcome='success'")
            if self.severity:
                raise ValueError("severity must be blank when outcome='success'")
            if self.failure_cause_detail:
                raise ValueError("failure_cause_detail must be blank when outcome='success'")

        context = info.context or {}
        composed_assembly = context.get("composed_assembly")
        if composed_assembly is True and self.stage_reached is None:
            raise ValueError(
                "stage_reached is required for composed-assembly attachments "
                "(use 0 if the first stage was never completed)"
            )
        if composed_assembly is False and self.stage_reached is not None:
            raise ValueError(
                "stage_reached is only meaningful for composed-assembly "
                "attachments and must be left blank here"
            )
        return self

    def to_csv_row(self) -> dict[str, str]:
        def s(v: Any) -> str:
            if v is None:
                return ""
            if isinstance(v, date):
                return v.isoformat()
            return str(v)

        return {col: s(getattr(self, col)) for col in TRIAL_COLUMNS}

    @classmethod
    def from_csv_row(cls, row: dict[str, str], *, context: dict | None = None) -> "TrialAnnotation":
        def n(v: str | None) -> str | None:
            return v if v not in (None, "") else None

        data = {
            "trial_id": row.get("trial_id", ""),
            "lab_id": row.get("lab_id", ""),
            "platform_id": row.get("platform_id", ""),
            "attachment_id": row.get("attachment_id", ""),
            "date": n(row.get("date")),
            "operator_name": row.get("operator_name") or "",
            "outcome": n(row.get("outcome")),
            "failure_cause": n(row.get("failure_cause")),
            "failure_cause_detail": row.get("failure_cause_detail") or "",
            "severity": n(row.get("severity")),
            "completion_time_s": n(row.get("completion_time_s")),
            "completion_source": n(row.get("completion_source")),
            "n_attempts": n(row.get("n_attempts")),
            "n_regrasps": n(row.get("n_regrasps")),
            "stage_reached": n(row.get("stage_reached")),
            "strategy": n(row.get("strategy")),
            "annotator_name": row.get("annotator_name") or "",
            "notes": row.get("notes") or "",
            "annotated_at": n(row.get("annotated_at")),
            "uploaded_at": n(row.get("uploaded_at")),
            "schema_version": row.get("schema_version") or ANNOTATION_SCHEMA_LEGACY,
        }
        return cls.model_validate(data, context=context or {})


class CameraSpec(BaseModel):
    name: str
    resolution: Optional[str] = None
    encoding: Optional[str] = None
    fps: Optional[float] = None
    position: Optional[str] = None
    orientation: Optional[str] = None
    file: Optional[str] = None 


class EpisodeHeader(BaseModel):
    """Header attributes stored on the .h5 file's root, merged from
    robot_profile.yaml (snapshotted at creation time) + episode-specific
    fields."""

    model_config = {"use_enum_values": True}

    lab_id: str
    platform_id: str
    session_id: str
    episode_id: str
    trial_id: str
    task_ids: list[str] = Field(default_factory=list)

    hiveboard_version: Optional[str] = None
    board_mounting: Optional[str] = None
    board_fabrication: dict[str, Any] = Field(default_factory=dict)
    manipulator: dict[str, Any] = Field(default_factory=dict)
    end_effector: dict[str, Any] = Field(default_factory=dict)
    low_level: dict[str, Any] = Field(default_factory=dict)
    control_mode: Optional[str] = None
    policy: Optional[str] = None
    robot_name: Optional[str] = None
    gripper_name: Optional[str] = None
    is_biarm: Optional[bool] = None
    uses_mobile_base: Optional[bool] = None
    control_freq: Optional[float] = None
    action_space: list[str] = Field(default_factory=list)
    action_joint_names: list[str] = Field(default_factory=list)
    orientation_representation: Optional[str] = None
    robot_state_orientation_representation: Optional[str] = None
    gains: dict[str, Any] = Field(default_factory=dict)
    intrinsic_calibration_matrix: dict[str, Any] = Field(default_factory=dict)
    extrinsic_calibration_matrix: dict[str, Any] = Field(default_factory=dict)
    collection_mode: Optional[str] = None
    manual_timer_s: Optional[float] = None
    cameras: list[dict[str, Any]] = Field(default_factory=list)
    units_and_frames: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime
    schema_version: str = EPISODE_SCHEMA_LEGACY
    datahive_version: str = "0.1.0"
