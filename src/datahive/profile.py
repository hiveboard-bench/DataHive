"""samples/robot_profile.yaml: the rig description filled in ONCE per lab.

Every episode snapshots this file's contents into its own header at
creation time (see episode.EpisodeWriter), so later edits to
robot_profile.yaml never retroactively change already-recorded episodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from datahive.errors import ProfileIncomplete, ProfileMissing
from datahive.paths import profile_path

SKELETON: dict[str, Any] = {
    "manipulator": {
        "model": None,
        "dof": None,
        "joint_names": [],
    },
    "end_effector": {
        "type": None,  # gripper | dexterous_hand | prosthetic_hand
        "actuated_dof": None,
        "command_modality": None,  # binary | position | velocity
    },
    "low_level": {
        "mode": None,  # stock | custom -- MANDATORY, never leave empty
        "controller_type": None,
        "rate_hz": None,
        "gains": None,
    },
    "control_mode": None,  # cartesian_impedance | osc | joint_position | ...
    "policy": None,  # teleop_<device> | vla_<name> | diffusion_<name> | null
    "cameras": [],  # [{name, resolution, encoding, fps, position, orientation}]
    "board_mounting": None,  # horizontal | vertical
    "hiveboard_version": None,
    "units_and_frames": {
        "joint_position": "rad",
        "joint_velocity": "rad/s",
        "joint_torque_or_current": "N*m",
        "ee_pose": "position_m_quaternion_xyzw_in_base_frame",
        "timestamp": "monotonic_seconds",
    },
    "platform_id": None,
}


@dataclass
class RobotProfile:
    manipulator: dict[str, Any] = field(default_factory=dict)
    end_effector: dict[str, Any] = field(default_factory=dict)
    low_level: dict[str, Any] = field(default_factory=dict)
    control_mode: str | None = None
    policy: str | None = None
    cameras: list[dict[str, Any]] = field(default_factory=list)
    board_mounting: str | None = None
    hiveboard_version: str | None = None
    units_and_frames: dict[str, Any] = field(default_factory=dict)
    platform_id: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RobotProfile":
        return cls(
            manipulator=raw.get("manipulator") or {},
            end_effector=raw.get("end_effector") or {},
            low_level=raw.get("low_level") or {},
            control_mode=raw.get("control_mode"),
            policy=raw.get("policy"),
            cameras=raw.get("cameras") or [],
            board_mounting=raw.get("board_mounting"),
            hiveboard_version=raw.get("hiveboard_version"),
            units_and_frames=raw.get("units_and_frames") or {},
            platform_id=raw.get("platform_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manipulator": self.manipulator,
            "end_effector": self.end_effector,
            "low_level": self.low_level,
            "control_mode": self.control_mode,
            "policy": self.policy,
            "cameras": self.cameras,
            "board_mounting": self.board_mounting,
            "hiveboard_version": self.hiveboard_version,
            "units_and_frames": self.units_and_frames,
            "platform_id": self.platform_id,
        }


def write_profile_skeleton(samples_root: Path, *, force: bool = False) -> Path:
    path = profile_path(samples_root)
    if path.exists() and not force:
        raise FileExistsError(
            f"{path} already exists. Pass --force to overwrite it "
            "(this will not touch already-recorded episodes)."
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Filled in ONCE for this rig. Every episode snapshots these values\n"
        "# at creation time; editing this file only affects episodes recorded\n"
        "# afterward.\n"
        + yaml.safe_dump(SKELETON, sort_keys=False),
        encoding="utf-8",
    )
    return path


def _incompleteness_problems(raw: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    low_level = raw.get("low_level") or {}
    if not low_level.get("mode"):
        problems.append(
            "low_level.mode is not set (must be 'stock' or 'custom'). An "
            "undocumented low-level control layer makes the data useless "
            "for comparison."
        )
    manipulator = raw.get("manipulator") or {}
    if not manipulator.get("joint_names"):
        problems.append("manipulator.joint_names is empty.")
    if not raw.get("cameras"):
        problems.append("cameras is empty -- at least one camera must be described.")
    return problems


def load_profile(samples_root: Path, *, allow_incomplete: bool = False) -> RobotProfile:
    path = profile_path(samples_root)
    if not path.is_file():
        raise ProfileMissing(
            f"No robot profile found at {path}. Run `datahive new-profile` "
            "and fill it in before recording or validating episodes."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems = _incompleteness_problems(raw)
    if problems and not allow_incomplete:
        bullets = "\n".join(f"  - {p}" for p in problems)
        raise ProfileIncomplete(
            f"{path} is incomplete:\n{bullets}\n\n"
            "Fill in these fields before recording or validating episodes."
        )
    return RobotProfile.from_dict(raw)


def profile_snapshot(profile: RobotProfile) -> dict[str, Any]:
    """Plain, JSON-serializable dict to be copied verbatim into an episode's
    header at creation time."""
    return profile.to_dict()
