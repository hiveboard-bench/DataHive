"""samples/robot_profile.yaml: the rig description filled in ONCE per lab.

Every episode snapshots this file's contents into its own header at
creation time (see episode.EpisodeWriter), so later edits to
robot_profile.yaml never retroactively change already-recorded episodes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from datahive.errors import ProfileIncomplete, ProfileMissing
from datahive.paths import profile_path

ARM_ACTIONS = ("joint_position", "joint_velocity", "cartesian_position", "cartesian_velocity")
GRIPPER_ACTIONS = ("gripper_position", "gripper_velocity", "gripper_binary")
BASE_ACTIONS = ("base_velocity", "base_position")


def action_space_problems(action_space: Any, uses_mobile_base: Any = None) -> list[str]:
    """oopsie's rule: >=1 arm action, >=1 gripper action, <=1 base action, no
    other keys; a mobile base needs a base action."""
    if not isinstance(action_space, (list, tuple)) or not action_space:
        return [
            "action_space is empty: choose at least one arm action and one gripper action."
        ]
    s = set(action_space)
    problems: list[str] = []
    unknown = sorted(s - set(ARM_ACTIONS) - set(GRIPPER_ACTIONS) - set(BASE_ACTIONS))
    if unknown:
        problems.append(f"action_space has unknown action(s): {unknown}.")
    if not s & set(ARM_ACTIONS):
        problems.append(f"action_space needs at least one arm action ({', '.join(ARM_ACTIONS)}).")
    if not s & set(GRIPPER_ACTIONS):
        problems.append(f"action_space needs at least one gripper action ({', '.join(GRIPPER_ACTIONS)}).")
    if len(s & set(BASE_ACTIONS)) > 1:
        problems.append("action_space may contain at most one base action.")
    if uses_mobile_base is True and not s & set(BASE_ACTIONS):
        problems.append("The platform has a mobile base, so action_space must include a base action.")
    return problems


SKELETON: dict[str, Any] = {
    "platform_id": None,  # derived automatically from robot_name + gripper_name
    "robot_name": None,  # REQUIRED: identifier for the robot platform, e.g. "franka_panda"
    "gripper_name": None,  # REQUIRED: identifier for the gripper, e.g. "robotiq_2f_85"
    "is_biarm": None,  # REQUIRED: true | false
    "uses_mobile_base": None,  # REQUIRED: true | false
    "control_freq": None,  # REQUIRED: control/recording frequency in Hz
    "action_joint_names": [],  # optional: what each index of the joint action vector is
    "orientation_representation": None,  # REQUIRED when action_space has cartesian_position
    "robot_state_orientation_representation": None,  # recommended: same options, for the recorded end-effector pose
    "gains": None,  # optional: {joint_position: {kp, kd}} | {joint_velocity: {kv}} | {osc: {kp_pos, kd_pos, kp_ori, kd_ori}}
    "intrinsic_calibration_matrix": None,  # optional: {camera: 3x3}
    "extrinsic_calibration_matrix": None,  # optional: {camera: 4x4}
    "action_space": [],  # REQUIRED: what the policy outputs, e.g. [joint_position, gripper_binary]
    "manipulator": {
        "model": None,
        "dof": None,  # derived from len(joint_names)
        "joint_names": [],  # optional
    },
    "end_effector": {
        "type": None,  # gripper | dexterous_hand | prosthetic_hand | other
        "type_description": None,  # REQUIRED when type is other
        "actuated_dof": None,
        "command_modality": None,  # binary | position | velocity
    },
    "low_level": {
        "mode": None,  # stock | custom -- MANDATORY, never leave empty
        "controller_type": None,
        "custom_description": None,  # REQUIRED when mode is custom
    },
    "control_mode": None,  # cartesian_impedance | osc | joint_position | ...
    "policy": None,  # REQUIRED default; override per recording with EpisodeWriter(policy=...)
    "cameras": [],  # [{name, position, orientation}]; resolution/fps/encoding are read from the videos
    "board_mounting": None,  # horizontal | vertical
    "hiveboard_version": None,
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
    robot_name: str | None = None
    gripper_name: str | None = None
    is_biarm: bool | None = None
    uses_mobile_base: bool | None = None
    control_freq: float | None = None
    action_space: list[str] = field(default_factory=list)
    action_joint_names: list[str] = field(default_factory=list)
    orientation_representation: str | None = None
    robot_state_orientation_representation: str | None = None
    gains: dict[str, Any] | None = None
    intrinsic_calibration_matrix: dict[str, Any] | None = None
    extrinsic_calibration_matrix: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RobotProfile":
        manipulator = dict(raw.get("manipulator") or {})
        if manipulator.get("joint_names"):
            manipulator["dof"] = len(manipulator["joint_names"])
        return cls(
            manipulator=manipulator,
            end_effector=raw.get("end_effector") or {},
            low_level=raw.get("low_level") or {},
            control_mode=raw.get("control_mode"),
            policy=raw.get("policy"),
            cameras=raw.get("cameras") or [],
            board_mounting=raw.get("board_mounting"),
            hiveboard_version=raw.get("hiveboard_version"),
            board_fabrication=raw.get("board_fabrication") or {},
            units_and_frames=raw.get("units_and_frames") or {},
            platform_id=raw.get("platform_id") or derive_platform_id(raw.get("robot_name") or manipulator.get("model"), raw.get("gripper_name")) or None,
            robot_name=raw.get("robot_name") or manipulator.get("model"),
            gripper_name=raw.get("gripper_name"),
            is_biarm=raw.get("is_biarm"),
            uses_mobile_base=raw.get("uses_mobile_base"),
            control_freq=raw.get("control_freq"),
            action_space=list(raw.get("action_space") or []),
            action_joint_names=list(raw.get("action_joint_names") or []),
            orientation_representation=raw.get("orientation_representation"),
            robot_state_orientation_representation=raw.get("robot_state_orientation_representation"),
            gains=raw.get("gains") or None,
            intrinsic_calibration_matrix=raw.get("intrinsic_calibration_matrix") or None,
            extrinsic_calibration_matrix=raw.get("extrinsic_calibration_matrix") or None,
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
            "robot_name": self.robot_name,
            "gripper_name": self.gripper_name,
            "is_biarm": self.is_biarm,
            "uses_mobile_base": self.uses_mobile_base,
            "control_freq": self.control_freq,
            "action_space": self.action_space,
            "action_joint_names": self.action_joint_names,
            "orientation_representation": self.orientation_representation,
            "robot_state_orientation_representation": self.robot_state_orientation_representation,
            "gains": self.gains,
            "intrinsic_calibration_matrix": self.intrinsic_calibration_matrix,
            "extrinsic_calibration_matrix": self.extrinsic_calibration_matrix,
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


_RESOLUTION_RE = re.compile(r"^\s*(\d{2,5})\s*[xX×*]\s*(\d{2,5})\s*$")


GAIN_CONTROLLERS: dict[str, tuple[str, ...]] = {
    "joint_position": ("kp", "kd"),
    "joint_velocity": ("kv",),
    "osc": ("kp_pos", "kd_pos", "kp_ori", "kd_ori"),
}


def _is_number_list(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and len(v) > 0 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)


def gains_problems(gains: Any, joint_names: list[str] | None = None) -> list[str]:
    """Optional. A mapping of the active controller to arrays of numbers:
    joint_position: kp, kd; joint_velocity: kv; osc: kp_pos, kd_pos, kp_ori, kd_ori."""
    if gains in (None, "", {}):
        return []
    if not isinstance(gains, dict):
        return [f"gains must be a mapping like {{joint_position: {{kp: [...], kd: [...]}}}}, not {type(gains).__name__}."]
    problems: list[str] = []
    for controller, values in gains.items():
        if controller not in GAIN_CONTROLLERS:
            problems.append(f"gains has unknown controller '{controller}' (use {', '.join(GAIN_CONTROLLERS)}).")
            continue
        expected = GAIN_CONTROLLERS[controller]
        if not isinstance(values, dict):
            problems.append(f"gains.{controller} must be a mapping with {', '.join(expected)}.")
            continue
        extra = sorted(set(values) - set(expected))
        if extra:
            problems.append(f"gains.{controller} has unknown field(s) {extra}; expected {list(expected)}.")
        for name in expected:
            arr = values.get(name)
            if not _is_number_list(arr):
                problems.append(f"gains.{controller}.{name} must be a list of numbers.")
            elif controller != "osc" and joint_names and len(arr) != len(joint_names):
                problems.append(f"gains.{controller}.{name} has {len(arr)} values but there are {len(joint_names)} joint names.")
    return problems


def calibration_problems(raw: dict[str, Any]) -> list[str]:
    """Optional per-camera matrices: intrinsic 3x3, extrinsic 4x4, keyed by camera name."""
    names = {c.get("name") for c in raw.get("cameras") or []}
    problems: list[str] = []
    for key, size in (("intrinsic_calibration_matrix", 3), ("extrinsic_calibration_matrix", 4)):
        value = raw.get(key)
        if not value:
            continue
        if not isinstance(value, dict):
            problems.append(f"{key} must map camera names to matrices.")
            continue
        for cam, matrix in value.items():
            if cam not in names:
                problems.append(f"{key} has a matrix for '{cam}', which is not a camera in this profile.")
            ok = isinstance(matrix, (list, tuple)) and len(matrix) == size and all(
                isinstance(row, (list, tuple)) and len(row) == size
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in row) for row in matrix)
            if not ok:
                problems.append(f"{key}['{cam}'] must be a {size}x{size} matrix of numbers.")
    return problems


ORIENTATION_OPTIONS = {
    "quat": "Quaternion, scalar-last, shape (4,)",
    "matrix": "Rotation matrix, shape (3, 3)",
    "rot6d": "First two columns of the rotation matrix, shape (6,) (used by openpi)",
    "rotvec": "Axis-angle, shape (3,)",
    "euler_xyz": "Euler angles, extrinsic xyz, shape (3,)",
    "euler_zyx": "Euler angles, extrinsic zyx, shape (3,)",
    "euler_xyx": "Euler angles, extrinsic xyx, shape (3,)",
    "euler_XYZ": "Euler angles, intrinsic XYZ, shape (3,)",
    "euler_ZYX": "Euler angles, intrinsic ZYX, shape (3,)",
    "euler_XYX": "Euler angles, intrinsic XYX, shape (3,)",
}
_EULER_RE = re.compile(r"^euler_(?:[xyz]{3}|[XYZ]{3})$")


def orientation_problem(value: Any) -> str | None:
    """None if `value` is a valid orientation representation: quat, matrix, rot6d,
    rotvec, or euler_<order> (lowercase = extrinsic, uppercase = intrinsic)."""
    v = str(value)
    if v in ("quat", "matrix", "rot6d", "rotvec"):
        return None
    if _EULER_RE.match(v) and v[6] != v[7] and v[7] != v[8]:
        return None
    return f"'{v}' is not a valid orientation representation (use quat, matrix, rot6d, rotvec or euler_<xyz order>)."


def derive_platform_id(robot_name: Any, gripper_name: Any) -> str:
    """HiveBoard's platform_id, built from the robot and gripper names
    (e.g. 'Franka Panda' + 'Robotiq 2F-85' -> 'franka_panda_robotiq_2f_85')."""
    def slug(v: Any) -> str:
        return re.sub(r"[^a-z0-9]+", "_", str(v or "").lower()).strip("_")
    return "_".join(x for x in (slug(robot_name), slug(gripper_name)) if x)


def normalize_resolution(value: Any) -> Any:
    """'1280 x 720', '1280X720' and '1280×720' all become '1280x720'; anything
    that does not look like a resolution is returned unchanged (and reported
    by incompleteness_problems)."""
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{int(value[0])}x{int(value[1])}"
    m = _RESOLUTION_RE.match(str(value)) if value not in (None, "") else None
    return f"{m.group(1)}x{m.group(2)}" if m else value


def _as_bool(value: Any) -> Any:
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("yes", "true", "1"):
            return True
        if v in ("no", "false", "0"):
            return False
        return None
    return value


def normalize_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Cleans a profile dict before it is written: derives dof from the joint
    names, normalizes camera resolution/fps and trims names."""
    out = dict(data)
    manipulator = dict(out.get("manipulator") or {})
    names = [str(n).strip() for n in manipulator.get("joint_names") or [] if str(n).strip()]
    manipulator["joint_names"] = names
    manipulator["dof"] = len(names) or None
    out["manipulator"] = manipulator
    cameras = []
    for cam in out.get("cameras") or []:
        cam = dict(cam)
        if cam.get("name"):
            cam["name"] = str(cam["name"]).strip()
        cam["resolution"] = normalize_resolution(cam.get("resolution"))
        if cam.get("fps") not in (None, ""):
            try:
                fps = float(cam["fps"])
                cam["fps"] = int(fps) if fps.is_integer() else fps
            except (TypeError, ValueError):
                pass
        cameras.append({k: v for k, v in cam.items() if v is not None})
    out["cameras"] = cameras
    for key in ("platform_id", "robot_name", "gripper_name"):
        if out.get(key):
            out[key] = str(out[key]).strip()
    out["platform_id"] = out.get("platform_id") or derive_platform_id(out.get("robot_name") or manipulator.get("model"), out.get("gripper_name")) or None
    # robot_name is the single source of truth for the arm's identity.
    if out.get("robot_name"):
        manipulator["model"] = out["robot_name"]
    elif manipulator.get("model"):
        out["robot_name"] = manipulator["model"]
    for key in ("is_biarm", "uses_mobile_base"):
        out[key] = _as_bool(out.get(key))
    actions: list[str] = []
    for a in out.get("action_space") or []:
        a = str(a).strip()
        if a and a not in actions:
            actions.append(a)
    out["action_space"] = actions
    for key in ("orientation_representation", "robot_state_orientation_representation"):
        out[key] = (str(out[key]).strip() or None) if out.get(key) else None
    for key in ("gains", "intrinsic_calibration_matrix", "extrinsic_calibration_matrix"):
        out[key] = out.get(key) or None
    out["action_joint_names"] = [str(n).strip() for n in out.get("action_joint_names") or [] if str(n).strip()]
    if out.get("control_freq") not in (None, ""):
        try:
            hz = float(out["control_freq"])
            out["control_freq"] = int(hz) if hz.is_integer() else hz
        except (TypeError, ValueError):
            pass
    else:
        out["control_freq"] = None
    return out


def incompleteness_problems(raw: dict[str, Any]) -> list[str]:
    """The mandatory-field checks shared by `datahive validate`, the CLI's
    profile loader, and the GUI's profile editor (so all three agree on
    what "complete" means)."""
    problems: list[str] = []
    for key, hint in (("robot_name", "e.g. franka_panda"), ("gripper_name", "e.g. robotiq_2f_85")):
        if not str(raw.get(key) or "").strip():
            problems.append(f"{key} is not set ({hint}).")
    for key in ("is_biarm", "uses_mobile_base"):
        if not isinstance(raw.get(key), bool):
            problems.append(f"{key} must be answered yes or no.")
    try:
        if float(raw.get("control_freq")) <= 0:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("control_freq must be a number greater than 0 (Hz).")
    action_names = [str(n).strip() for n in raw.get("action_joint_names") or []]
    if action_names and len({n.casefold() for n in action_names}) != len(action_names):
        problems.append("action_joint_names contains duplicates.")
    space = raw.get("action_space") or []
    if "cartesian_position" in space and not raw.get("orientation_representation"):
        problems.append("orientation_representation is required when action_space contains cartesian_position.")
    if {"cartesian_position", "cartesian_velocity"} & set(space) and not raw.get("robot_state_orientation_representation"):
        problems.append(
            "robot_state_orientation_representation is required when action_space contains cartesian_position "
            "or cartesian_velocity (a Cartesian action implies a Cartesian pose in the robot state)."
        )
    for key in ("orientation_representation", "robot_state_orientation_representation"):
        if raw.get(key):
            bad = orientation_problem(raw[key])
            if bad:
                problems.append(f"{key}: {bad}")
    problems.extend(action_space_problems(raw.get("action_space"), raw.get("uses_mobile_base")))
    if not str(raw.get("policy") or "").strip():
        problems.append("policy is not set (e.g. teleop_spacemouse, vla_pi0, diffusion_policy).")
    low_level = raw.get("low_level") or {}
    if not low_level.get("mode"):
        problems.append(
            "low_level.mode is not set (must be 'stock' or 'custom'). An "
            "undocumented low-level control layer makes the data useless "
            "for comparison."
        )
    if low_level.get("mode") == "custom" and not str(low_level.get("custom_description") or "").strip():
        problems.append(
            "low_level.custom_description is empty: describe your custom controller "
            "(what it does, its loop, anything a reader needs to compare results)."
        )
    manipulator = raw.get("manipulator") or {}
    joint_names = [str(n).strip() for n in manipulator.get("joint_names") or []]
    if joint_names and len({n.casefold() for n in joint_names}) != len(joint_names):
        problems.append("manipulator.joint_names contains duplicates.")
    space = raw.get("action_space") or []
    if {"joint_position", "joint_velocity"} & set(space):
        if not joint_names:
            problems.append("robot_state_joint_names (the joint names) are required when action_space contains joint_position or joint_velocity.")
        if not action_names:
            problems.append("action_joint_names are required when action_space contains joint_position or joint_velocity.")
    problems.extend(gains_problems(raw.get("gains"), joint_names))
    problems.extend(calibration_problems(raw))
    ee = raw.get("end_effector") or {}
    if not ee.get("type"):
        problems.append("end_effector.type is not set (gripper, dexterous_hand, prosthetic_hand or other).")
    elif ee.get("type") == "other" and not str(ee.get("type_description") or "").strip():
        problems.append("end_effector.type_description is required when the type is other: describe the end effector.")
    try:
        if int(ee.get("actuated_dof")) < 1:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("end_effector.actuated_dof must be a whole number of at least 1.")
    if not ee.get("command_modality"):
        problems.append("end_effector.command_modality is not set (binary, position or velocity).")
    cameras = raw.get("cameras") or []
    if not cameras:
        problems.append("cameras is empty -- at least one camera must be described.")
    seen: set[str] = set()
    for i, cam in enumerate(cameras):
        label = cam.get("name") or f"camera {i + 1}"
        if not cam.get("name"):
            problems.append(f"{label} has no name.")
        elif cam["name"].casefold() in seen:
            problems.append(f"Camera name '{cam['name']}' is used twice (names are compared ignoring case).")
        seen.add(str(cam.get("name") or "").casefold())
    problems.extend(camera_consistency_problems(cameras))
    return problems

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
    merged: dict[str, Any] = {**SKELETON, **normalize_profile(data)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(merged, sort_keys=False), encoding="utf-8")
    return path


def profile_snapshot(profile: RobotProfile) -> dict[str, Any]:
    """Plain, JSON-serializable dict to be copied verbatim into an episode's
    header at creation time."""
    return profile.to_dict()
