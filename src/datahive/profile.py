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
    # Same field names as HiveBoard's Evaluation Runner "setup details"
    # (https://hiveboard-bench.github.io/hivedocs/benchmark/evaluation-runner),
    # so a lab's submission there and its DataHive profile stay consistent.
    "board_fabrication": {
        "printer": None,  # manufacturer and model
        "material": None,  # filament type and manufacturer
        "print_settings": None,  # nozzle, layer height, walls, infill, part orientation
        "post_processing": None,  # sanding, lubrication, dimensional adjustments, or "none"
        "calibration_notes": None,  # relevant calibration or setup changes
    },
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
    board_fabrication: dict[str, Any] = field(default_factory=dict)
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
            board_fabrication=raw.get("board_fabrication") or {},
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
            "board_fabrication": self.board_fabrication,
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


def camera_consistency_problems(cameras: list[dict[str, Any]]) -> list[str]:
    """All cameras on a rig (and so on every episode it records) must share
    the same resolution and fps -- otherwise per-camera framing and timing
    aren't comparable across the recording. A no-op for 0 or 1 cameras."""
    problems: list[str] = []
    if not cameras or len(cameras) < 2:
        return problems

    def _values(field: str) -> dict[str, Any]:
        return {c.get("name") or f"camera {i}": c.get(field) for i, c in enumerate(cameras)}

    for field, label in (("resolution", "resolution"), ("fps", "fps")):
        values = _values(field)
        distinct = {v for v in values.values()}
        if len(distinct) > 1:
            detail = ", ".join(f"{name}={value!r}" for name, value in values.items())
            problems.append(f"Cameras have inconsistent {label} ({detail}) -- all cameras must match.")
    return problems


def incompleteness_problems(raw: dict[str, Any]) -> list[str]:
    """The mandatory-field checks shared by `datahive validate`, the CLI's
    profile loader, and the GUI's profile editor (so all three agree on
    what "complete" means)."""
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
    cameras = raw.get("cameras") or []
    if not cameras:
        problems.append("cameras is empty -- at least one camera must be described.")
    problems.extend(camera_consistency_problems(cameras))
    return problems


# Back-compat alias (kept private-looking name in case other code imports it).
_incompleteness_problems = incompleteness_problems


def read_raw_profile(samples_root: Path) -> dict[str, Any] | None:
    """Returns the profile file's raw dict, or None if it doesn't exist yet."""
    path = profile_path(samples_root)
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_profile(samples_root: Path, *, allow_incomplete: bool = False) -> RobotProfile:
    path = profile_path(samples_root)
    if not path.is_file():
        raise ProfileMissing(
            f"No robot profile found at {path}. Run `datahive new-profile` "
            "(or create one from the web interface) and fill it in before "
            "recording or validating episodes."
        )
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    problems = incompleteness_problems(raw)
    if problems and not allow_incomplete:
        bullets = "\n".join(f"  - {p}" for p in problems)
        raise ProfileIncomplete(
            f"{path} is incomplete:\n{bullets}\n\n"
            "Fill in these fields before recording or validating episodes."
        )
    return RobotProfile.from_dict(raw)


def save_profile(samples_root: Path, data: dict[str, Any]) -> Path:
    """Writes a full robot_profile.yaml from a plain dict (e.g. the payload
    submitted by the web interface's profile form). Missing top-level keys
    are filled in from the skeleton so the file always has the full shape,
    and the same completeness rules apply as for a hand-edited file --
    callers should check `incompleteness_problems()` themselves if they
    need to warn the user, `save_profile` does not block on it."""
    path = profile_path(samples_root)
    merged: dict[str, Any] = {**SKELETON, **data}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    return path


def profile_snapshot(profile: RobotProfile) -> dict[str, Any]:
    """Plain, JSON-serializable dict to be copied verbatim into an episode's
    header at creation time."""
    return profile.to_dict()
