"""Pydantic v2 schema for episode headers and trial annotations."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


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


TRIAL_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "lab_id",
    "platform_id",
    "attachment_id",
    "date",
    "outcome",
    "failure_cause",
    "completion_time_s",
    "n_attempts",
    "n_regrasps",
    "stage_reached",
    "strategy",
    "notes",
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
    outcome: Outcome
    failure_cause: Optional[FailureCause] = None
    completion_time_s: Optional[float] = Field(default=None, gt=0)
    n_attempts: Optional[int] = Field(default=None, ge=0)
    n_regrasps: Optional[int] = Field(default=None, ge=0)
    stage_reached: Optional[int] = Field(default=None, ge=0)
    strategy: Strategy
    notes: str = ""

    @model_validator(mode="after")
    def _cross_field_rules(self, info):
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
        else:
            if self.failure_cause:
                raise ValueError("failure_cause must be blank when outcome='success'")
            if self.completion_time_s is None:
                raise ValueError("completion_time_s is required when outcome='success'")

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
            "outcome": n(row.get("outcome")),
            "failure_cause": n(row.get("failure_cause")),
            "completion_time_s": n(row.get("completion_time_s")),
            "n_attempts": n(row.get("n_attempts")),
            "n_regrasps": n(row.get("n_regrasps")),
            "stage_reached": n(row.get("stage_reached")),
            "strategy": n(row.get("strategy")),
            "notes": row.get("notes") or "",
        }
        return cls.model_validate(data, context=context or {})


class CameraSpec(BaseModel):
    name: str
    resolution: Optional[str] = None
    encoding: Optional[str] = None
    fps: Optional[float] = None
    position: Optional[str] = None
    orientation: Optional[str] = None
    file: Optional[str] = None  # basename of the referenced .mp4


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
    manipulator: dict[str, Any] = Field(default_factory=dict)
    end_effector: dict[str, Any] = Field(default_factory=dict)
    low_level: dict[str, Any] = Field(default_factory=dict)
    control_mode: Optional[str] = None
    policy: Optional[str] = None
    cameras: list[dict[str, Any]] = Field(default_factory=list)
    units_and_frames: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime
    datahive_version: str = "0.1.0"
