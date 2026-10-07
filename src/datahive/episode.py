"""Episode file (.h5) read/write.

Header attributes are stored as JSON strings on the root group's attrs
(dicts/lists) or as native scalars. Robot states and video references live
under /observations, while actions live under /actions. Video files are sibling
.mp4 files referenced by filename in the header and observations/video_paths.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import h5py
import numpy as np

from datahive.paths import EpisodePaths, resolve_episode_paths
from datahive.profile import RobotProfile, load_profile, profile_snapshot
from datahive.schema import EPISODE_SCHEMA_CURRENT, EpisodeHeader

resolve_episode = resolve_episode_paths

PROPRIOCEPTION_FIELDS = (
    "timestamp",
    "joint_position",
    "joint_velocity",
    "joint_torque_or_current",
    "ee_pose",
    "ee_state",
)
def _orientation_width(representation: str | None) -> int:
    if representation == "quat":
        return 4
    if representation == "matrix":
        return 9
    if representation == "rot6d":
        return 6
    if representation == "rotvec" or (representation is not None and re.fullmatch(r"euler_[xyzXYZ]{3}", representation)):
        return 3
    raise ValueError(f"Unsupported or missing orientation_representation: {representation!r}")


def _action_widths(header: EpisodeHeader) -> dict[str, int | None]:
    """Return component widths from the episode's snapshotted action metadata."""
    widths: dict[str, int | None] = {}
    state_joints = list(header.manipulator.get("joint_names") or [])
    action_joints = list(header.action_joint_names or [])
    # Prefer the explicit command joint list (it can be a subset). Older
    # profiles sometimes included a trailing gripper entry in this list; if
    # its prefix exactly matches all state joints, treat that extra entry as
    # the separately described gripper command.
    if state_joints and len(action_joints) > len(state_joints) and action_joints[:len(state_joints)] == state_joints:
        arm_joints = len(state_joints)
    else:
        arm_joints = len(action_joints) or len(state_joints)
    gripper_dof = int((header.end_effector or {}).get("actuated_dof") or 1)
    for action in header.action_space:
        if action in {"joint_position", "joint_velocity", "joint_binary"}:
            if not arm_joints:
                raise ValueError(f"Cannot split {action}: the header has no joint names or DOF")
            widths[action] = arm_joints
        elif action == "cartesian_position":
            widths[action] = 3 + _orientation_width(header.orientation_representation)
        elif action == "cartesian_velocity":
            widths[action] = 6
        elif action in {"gripper_position", "gripper_velocity"}:
            widths[action] = gripper_dof
        elif action == "gripper_binary":
            widths[action] = 1
        elif action in {"base_velocity", "base_position"}:
            widths[action] = None  # Base conventions vary; infer a single base field from the remaining width.
        else:
            raise ValueError(f"Unsupported action_space entry: {action!r}")
    if len(widths) != len(header.action_space):
        raise ValueError("action_space contains duplicate action names")
    unresolved = [name for name, width in widths.items() if width is None]
    if len(unresolved) > 1:
        raise ValueError("Cannot infer dimensions when action_space has multiple base actions")
    return widths


def _split_action_target(header: EpisodeHeader, target) -> dict[str, np.ndarray]:
    """Split a flat action vector or validate a named mapping against the profile."""
    widths = _action_widths(header)
    if isinstance(target, Mapping):
        values = {str(name): np.atleast_1d(np.asarray(value)) for name, value in target.items()}
        missing = set(widths) - set(values)
        extra = set(values) - set(widths)
        if missing or extra:
            raise ValueError(f"Action fields do not match action_space (missing={sorted(missing)}, extra={sorted(extra)})")
        for name, width in widths.items():
            if width is not None and values[name].size != width:
                raise ValueError(f"{name} expects {width} value(s), got {values[name].size}")
            if width is None and values[name].size == 0:
                raise ValueError(f"{name} must have at least one value")
            if name in {"joint_binary", "gripper_binary"} and not np.isin(values[name], [0, 1, False, True]).all():
                raise ValueError(f"{name} values must be binary (0 or 1)")
        return values

    flat = np.asarray(target).reshape(-1)
    unresolved = next((name for name, width in widths.items() if width is None), None)
    known_width = sum(width for width in widths.values() if width is not None)
    if unresolved is not None:
        widths[unresolved] = len(flat) - known_width
        if widths[unresolved] <= 0:
            raise ValueError(f"Cannot infer a positive width for {unresolved}")
    expected = sum(int(width) for width in widths.values())
    if len(flat) != expected:
        raise ValueError(
            f"Action target has {len(flat)} value(s), but action_space requires {expected} "
            f"({', '.join(f'{name}={width}' for name, width in widths.items())})"
        )
    out: dict[str, np.ndarray] = {}
    offset = 0
    for name, width in widths.items():
        out[name] = flat[offset:offset + int(width)]
        offset += int(width)
    for name in {"joint_binary", "gripper_binary"} & out.keys():
        if not np.isin(out[name], [0, 1, False, True]).all():
            raise ValueError(f"{name} values must be binary (0 or 1)")
    return out


def group_path(h5file: h5py.File, name: str) -> str:
    """Resolve canonical HDF5 groups while remaining able to read older files."""
    paths = {
        "robot_states": ("observations/robot_states", "proprioception"),
        "actions": ("actions", "commands"),
        "video_paths": ("observations/video_paths",),
    }
    aliases = {
        "observations/robot_states": "robot_states", "proprioception": "robot_states",
        "commands": "actions",
    }
    name = aliases.get(name, name)
    candidates = paths.get(name, (name,))
    return next((path for path in candidates if path in h5file), candidates[0])


def canonicalize_layout(h5file: h5py.File) -> None:
    """Move legacy trajectory groups into the current HDF5 hierarchy."""
    observations = h5file.require_group("observations")
    moves = (("proprioception", "observations/robot_states"), ("commands", "actions"))
    for old, new in moves:
        if old in h5file and new not in h5file:
            h5file.move(old, new)
    observations.require_group("robot_states")
    observations.require_group("video_paths")
    h5file.require_group("actions")


def write_video_paths(h5file: h5py.File, cameras: list[dict]) -> None:
    """Store sibling video filenames as camera-named datasets."""
    group = h5file.require_group("observations").require_group("video_paths")
    for camera in cameras:
        name, filename = camera.get("name"), camera.get("file")
        if not name or not filename:
            continue
        if name in group:
            del group[name]
        group.create_dataset(name, data=filename, dtype=h5py.string_dtype("utf-8"))

_JSON_HEADER_FIELDS = {
    "task_ids",
    "manipulator",
    "end_effector",
    "low_level",
    "cameras",
    "units_and_frames",
    "board_fabrication",
    "action_space",
    "action_joint_names",
    "gains",
    "intrinsic_calibration_matrix",
    "extrinsic_calibration_matrix",
}

_NULLABLE_STRING_FIELDS = {
    "collection_mode", "manual_timer_s", "orientation_representation", "robot_state_orientation_representation", "policy", "board_mounting", "hiveboard_version", "control_mode",
    "robot_name", "gripper_name", "is_biarm", "uses_mobile_base", "control_freq",
}


def write_header(h5file: h5py.File, header: EpisodeHeader) -> None:
    data = header.model_dump(mode="json")
    for key, value in data.items():
        if key in _JSON_HEADER_FIELDS:
            h5file.attrs[key] = json.dumps(value)
        elif value is None:
            h5file.attrs[key] = ""
        else:
            h5file.attrs[key] = value
    h5file.attrs["_json_fields"] = json.dumps(sorted(_JSON_HEADER_FIELDS))


def read_header(h5_path: Path) -> EpisodeHeader:
    with h5py.File(h5_path, "r") as f:
        json_fields = set(json.loads(f.attrs.get("_json_fields", "[]")))
        data: dict[str, Any] = {}
        for key in f.attrs:
            if key == "_json_fields":
                continue
            raw = f.attrs[key]
            if key in json_fields:
                data[key] = json.loads(raw)
            elif isinstance(raw, str) and raw == "" and key in _NULLABLE_STRING_FIELDS:
                data[key] = None
            else:
                data[key] = raw.item() if hasattr(raw, "item") else raw
    return EpisodeHeader.model_validate(data)


def sample_rate_hz(h5_path: Path, group: str = "observations/robot_states") -> float | None:
    with h5py.File(h5_path, "r") as f:
        grp = f.get(group_path(f, group))
        if grp is None or "timestamp" not in grp:
            return None
        ts = np.asarray(grp["timestamp"][:]).reshape(-1)
    if len(ts) < 2:
        return None
    dt = np.median(np.diff(ts))
    if dt <= 0:
        return None
    return float(1.0 / dt)


def episode_stats(h5_path: Path, group: str = "observations/robot_states") -> dict[str, float | int | None]:
    """Cheap summary stats for the GUI's detail view: number of recorded
    steps, wall-clock duration, and sample rate."""
    n_steps = 0
    duration_s: float | None = None
    with h5py.File(h5_path, "r") as f:
        grp = f.get(group_path(f, group))
        if grp is not None and "timestamp" in grp:
            ts = np.asarray(grp["timestamp"][:]).reshape(-1)
            n_steps = int(ts.shape[0])
            if n_steps >= 2:
                duration_s = float(ts[-1] - ts[0])
    return {
        "n_steps": n_steps,
        "duration_s": duration_s,
        "sample_rate_hz": sample_rate_hz(h5_path, group=group),
    }


def episode_dataset_info(h5_path: Path) -> dict[str, dict]:
    """Per-field metadata arranged to match the HDF5 hierarchy,
    without reading the actual sample data -- used for the GUI's expanded
    Overview panel (h5py exposes shape/dtype/attrs without touching the
    underlying array)."""
    info: dict[str, dict] = {"observations": {"robot_states": {}, "video_paths": {}}, "actions": {}}
    with h5py.File(h5_path, "r") as f:
        for key in ("robot_states", "video_paths", "actions"):
            path = group_path(f, key)
            grp = f.get(path)
            target = info["observations"][key] if key in ("robot_states", "video_paths") else info["actions"]
            if grp is not None:
                for name, ds in grp.items():
                    entry = {"shape": list(ds.shape), "dtype": str(ds.dtype), "provenance": ds.attrs.get("provenance")}
                    if key == "video_paths":
                        raw = ds[()]
                        entry["value"] = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    target[name] = entry
    return info


def read_trajectory(
    h5_path: Path,
    *,
    group: str = "observations/robot_states",
    fields: list[str] | None = None,
    max_points: int | None = None,
) -> dict[str, list]:
    with h5py.File(h5_path, "r") as f:
        grp = f.get(group_path(f, group))
        if grp is None:
            return {}
        available = list(grp.keys())
        wanted = fields or available
        n = None
        result: dict[str, np.ndarray] = {}
        for name in wanted:
            if name not in grp:
                continue
            arr = grp[name][:]
            if arr.ndim == 2 and arr.shape[1] == 1:
                arr = arr.reshape(-1)  
            n = arr.shape[0] if n is None else n
            result[name] = arr

        if n and max_points and n > max_points:
            stride = max(1, n // max_points)
            idx = np.arange(0, n, stride)
            result = {k: v[idx] for k, v in result.items()}

    return {k: np.asarray(v).tolist() for k, v in result.items()}


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def content_hash(samples_root: Path, episode_id: str, session_id: str | None = None) -> str:
    """SHA-256 over a canonical manifest of the episode's .h5 + videos, plus
    its trials.csv annotation row (so re-annotating marks it dirty)."""
    from datahive.trials import get_row

    paths = resolve_episode_paths(samples_root, episode_id, session_id)
    manifest: list[dict[str, str]] = []
    for label, p in [("h5", paths.h5), *sorted(paths.videos.items())]:
        if p.exists():
            manifest.append(
                {"name": label, "size": str(p.stat().st_size), "sha256": _file_sha256(p)}
            )
    row = None
    try:
        header = read_header(paths.h5)
        row = get_row(paths.trials_csv, header.trial_id)
    except Exception:
        row = None
    manifest.append({"name": "trials_row", "value": json.dumps(row, sort_keys=True)})

    h = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def make_header(
    profile: RobotProfile, *, session_id: str, episode_id: str, trial_id: str,
    task_ids: list[str] | None, lab_id: str, policy: str | None = None,
    collection_mode: str | None = None,
) -> EpisodeHeader:
    """Header for a new episode: the current robot profile snapshotted, plus the
    episode identity. Shared by EpisodeWriter and manually uploaded episodes."""
    snapshot = profile_snapshot(profile)
    return EpisodeHeader(
        lab_id=lab_id,
        platform_id=snapshot.get("platform_id") or "",
        session_id=session_id,
        episode_id=episode_id,
        trial_id=trial_id,
        task_ids=task_ids or [],
        hiveboard_version=snapshot.get("hiveboard_version"),
        board_mounting=snapshot.get("board_mounting"),
        board_fabrication=snapshot.get("board_fabrication") or {},
        manipulator=snapshot.get("manipulator") or {},
        end_effector=snapshot.get("end_effector") or {},
        low_level=snapshot.get("low_level") or {},
        control_mode=snapshot.get("control_mode"),
        policy=policy if policy is not None else snapshot.get("policy"),
        robot_name=snapshot.get("robot_name"),
        gripper_name=snapshot.get("gripper_name"),
        is_biarm=snapshot.get("is_biarm"),
        uses_mobile_base=snapshot.get("uses_mobile_base"),
        control_freq=snapshot.get("control_freq"),
        action_space=snapshot.get("action_space") or [],
        action_joint_names=snapshot.get("action_joint_names") or [],
        orientation_representation=snapshot.get("orientation_representation"),
        robot_state_orientation_representation=snapshot.get("robot_state_orientation_representation"),
        gains=snapshot.get("gains") or {},
        intrinsic_calibration_matrix=snapshot.get("intrinsic_calibration_matrix") or {},
        extrinsic_calibration_matrix=snapshot.get("extrinsic_calibration_matrix") or {},
        cameras=snapshot.get("cameras") or [],
        units_and_frames=snapshot.get("units_and_frames") or {},
        created_at=datetime.now(timezone.utc),
        schema_version=EPISODE_SCHEMA_CURRENT,
        collection_mode=collection_mode,
    )


class EpisodeWriter:
    """Public library API for recording an episode. Snapshots the current
    robot_profile.yaml into the episode's own header at creation time, so
    later edits to the profile never retroactively change this episode."""

    def __init__(
        self,
        samples_root: Path,
        session_id: str,
        episode_id: str,
        *,
        trial_id: str,
        task_ids: list[str] | None = None,
        lab_id: str,
        profile: RobotProfile | None = None,
        policy: str | None = None,
        collection_mode: str | None = None,
    ):
        self.samples_root = Path(samples_root)
        self.session_id = session_id
        self.episode_id = episode_id

        if profile is None:
            profile = load_profile(self.samples_root)
        edir = self.samples_root / session_id / "episodes"
        edir.mkdir(parents=True, exist_ok=True)
        self.paths = EpisodePaths(
            samples_root=self.samples_root,
            session_id=session_id,
            episode_id=episode_id,
            session_dir=self.samples_root / session_id,
            h5=edir / f"{episode_id}.h5",
            trials_csv=self.samples_root / session_id / "trials.csv",
            setup_jpg=self.samples_root / session_id / "setup.jpg",
        )

        header = make_header(
            profile, session_id=session_id, episode_id=episode_id, trial_id=trial_id,
            task_ids=task_ids, lab_id=lab_id, policy=policy, collection_mode=collection_mode,
        )
        self.header = header
        # Never silently truncate an existing episode. A caller must choose a
        # fresh ID or explicitly remove the old file first.
        self._file = h5py.File(self.paths.h5, "x")
        write_header(self._file, header)
        observations = self._file.create_group("observations")
        self._proprio_grp = observations.create_group("robot_states")
        self._video_paths_grp = observations.create_group("video_paths")
        self._cmd_grp = self._file.create_group("actions")
        self._proprio_datasets: dict[str, h5py.Dataset] = {}
        self._cmd_datasets: dict[str, h5py.Dataset] = {}
        self._recorded_control_mode: str | None = None

    def _append(self, grp: h5py.Group, cache: dict, name: str, value: np.ndarray, provenance: str | None):
        value = np.atleast_1d(np.asarray(value))
        if name not in cache:
            maxshape = (None, *value.shape)
            ds = grp.create_dataset(
                name, shape=(0, *value.shape), maxshape=maxshape, dtype=value.dtype, chunks=True
            )
            if provenance:
                ds.attrs["provenance"] = provenance
            cache[name] = ds
        ds = cache[name]
        ds.resize(ds.shape[0] + 1, axis=0)
        ds[-1] = value

    def append_proprioception(
        self,
        *,
        timestamp: float,
        joint_position=None,
        joint_velocity=None,
        joint_torque_or_current=None,
        ee_pose=None,
        ee_state=None,
        provenance: dict[str, Literal["measured", "estimated"]],
    ) -> None:
        fields = {
            "timestamp": timestamp,
            "joint_position": joint_position,
            "joint_velocity": joint_velocity,
            "joint_torque_or_current": joint_torque_or_current,
            "ee_pose": ee_pose,
            "ee_state": ee_state,
        }
        for name, value in fields.items():
            if value is None:
                continue
            prov = "measured" if name == "timestamp" else provenance.get(name, "measured")
            self._append(self._proprio_grp, self._proprio_datasets, name, value, prov)

    def append_command(self, *, timestamp: float, target, control_mode: str) -> None:
        """Append one command, storing one dataset for each action_space entry.

        `target` may be a flat vector in the profile's action_space order or a
        mapping keyed by action name. Joint widths come from the arm joint
        names, gripper position/velocity widths from actuated_dof, and binary
        actions use one value for a gripper or one per arm joint.
        """
        action_values = _split_action_target(self.header, target)
        control_mode = str(control_mode).strip()
        if not control_mode:
            raise ValueError("control_mode must be a non-empty string")
        if self._recorded_control_mode and control_mode != self._recorded_control_mode:
            raise ValueError(
                f"control_mode changed during episode from {self._recorded_control_mode!r} "
                f"to {control_mode!r}; one episode must use a single control mode"
            )
        if self._recorded_control_mode is None:
            self._recorded_control_mode = control_mode
            self.header.control_mode = control_mode
            self._file.attrs["control_mode"] = control_mode
        self._append(self._cmd_grp, self._cmd_datasets, "timestamp", timestamp, "commanded")
        for name, value in action_values.items():
            self._append(self._cmd_grp, self._cmd_datasets, name, value, "commanded")

    def attach_video(self, camera_name: str, src: Path, *, move: bool = False) -> Path:
        if (not camera_name or camera_name in {".", ".."}
                or "/" in camera_name or "\\" in camera_name or "\x00" in camera_name):
            raise ValueError(f"Invalid camera name: {camera_name!r}")
        if not any(cam.get("name") == camera_name for cam in self.header.cameras):
            raise ValueError(f"Camera {camera_name!r} is not defined in the robot profile")
        src = Path(src)
        dest = self.paths.h5.parent / f"{self.episode_id}_cam_{camera_name}.mp4"
        if move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))
        self.paths.videos[camera_name] = dest
        if camera_name in self._video_paths_grp:
            del self._video_paths_grp[camera_name]
        self._video_paths_grp.create_dataset(camera_name, data=dest.name, dtype=h5py.string_dtype("utf-8"))

        from datahive.consistency import probe_video

        try:
            info = probe_video(dest)
        except ValueError:
            info = None
        for cam in self.header.cameras:
            if cam.get("name") == camera_name:
                cam["file"] = dest.name
                if info:
                    cam["resolution"] = f"{info['width']}x{info['height']}"
                    cam["fps"] = round(info["fps"], 3)
                    cam["encoding"] = info["encoding"]
                break
        if self._file is not None:
            self._file.attrs["cameras"] = json.dumps(self.header.cameras)

        return dest

    def close(self) -> None:
        if self._file:
            self._file.close()
            self._file = None

    def __enter__(self) -> "EpisodeWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
