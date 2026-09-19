"""Episode file (.h5) read/write.

Header attributes are stored as JSON strings on the root group's attrs
(dicts/lists) or as native scalars. Datasets live under /proprioception and
/commands. Video is never embedded -- each camera is a sibling .mp4,
referenced by filename in the header's `cameras` list.
"""

from __future__ import annotations

import hashlib
import json
import shutil
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
COMMAND_FIELDS = ("timestamp", "target", "control_mode")

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


def sample_rate_hz(h5_path: Path, group: str = "proprioception") -> float | None:
    with h5py.File(h5_path, "r") as f:
        grp = f.get(group)
        if grp is None or "timestamp" not in grp:
            return None
        ts = np.asarray(grp["timestamp"][:]).reshape(-1)
    if len(ts) < 2:
        return None
    dt = np.median(np.diff(ts))
    if dt <= 0:
        return None
    return float(1.0 / dt)


def episode_stats(h5_path: Path, group: str = "proprioception") -> dict[str, float | int | None]:
    """Cheap summary stats for the GUI's detail view: number of recorded
    steps, wall-clock duration, and sample rate."""
    n_steps = 0
    duration_s: float | None = None
    with h5py.File(h5_path, "r") as f:
        grp = f.get(group)
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


def episode_dataset_info(h5_path: Path) -> dict[str, dict[str, dict]]:
    """Per-field shape/dtype/provenance for /proprioception and /commands,
    without reading the actual sample data -- used for the GUI's expanded
    Overview panel (h5py exposes shape/dtype/attrs without touching the
    underlying array)."""
    info: dict[str, dict[str, dict]] = {}
    with h5py.File(h5_path, "r") as f:
        for group_name in ("proprioception", "commands"):
            fields: dict[str, dict] = {}
            grp = f.get(group_name)
            if grp is not None:
                for name, ds in grp.items():
                    fields[name] = {
                        "shape": list(ds.shape),
                        "dtype": str(ds.dtype),
                        "provenance": ds.attrs.get("provenance"),
                    }
            info[group_name] = fields
    return info


def read_trajectory(
    h5_path: Path,
    *,
    group: str = "proprioception",
    fields: list[str] | None = None,
    max_points: int | None = None,
) -> dict[str, list]:
    with h5py.File(h5_path, "r") as f:
        grp = f.get(group)
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
        self._file = h5py.File(self.paths.h5, "w")
        write_header(self._file, header)
        self._proprio_grp = self._file.create_group("proprioception")
        self._cmd_grp = self._file.create_group("commands")
        self._proprio_datasets: dict[str, h5py.Dataset] = {}
        self._cmd_datasets: dict[str, h5py.Dataset] = {}

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
        self._append(self._cmd_grp, self._cmd_datasets, "timestamp", timestamp, "commanded")
        self._append(self._cmd_grp, self._cmd_datasets, "target", target, "commanded")
        cm = np.frombuffer(control_mode.encode("utf-8"), dtype=np.uint8)
        self._append(self._cmd_grp, self._cmd_datasets, "control_mode", cm, "commanded")

    def attach_video(self, camera_name: str, src: Path, *, move: bool = False) -> Path:
        src = Path(src)
        dest = self.paths.h5.parent / f"{self.episode_id}_cam_{camera_name}.mp4"
        if move:
            shutil.move(str(src), str(dest))
        else:
            shutil.copy2(str(src), str(dest))
        self.paths.videos[camera_name] = dest

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
