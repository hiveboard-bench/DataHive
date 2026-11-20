"""LeRobot Dataset v3.0 reader.

Layout assumed (v3.0)::

    meta/info.json
    meta/tasks.parquet                       task_index -> task text
    meta/episodes/chunk-*/file-*.parquet     per-episode records
    data/chunk-*/file-*.parquet              frame table
    videos/<video_key>/chunk-*/file-*.mp4    many episodes per shard

Episode boundaries come from the per-camera ``videos/<key>/from_timestamp`` /
``to_timestamp`` columns, but the default segmentation is frame offsets
accumulated from each episode's ``length`` within its shard. LeRobot encodes
whole episodes before concatenating them, so a segment never crosses a shard,
and frame offsets are exact where nominal timestamps can drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pyarrow.parquet as pq

from .base import DatasetInfo, EpisodeMeta, SourceEpisode, VideoSegment

GRIPPER_HINTS = ("gripper", "finger", "jaw", "hand")


class LeRobotV3Source:
    kind = "lerobot_v3"

    def __init__(
        self,
        root: Path,
        *,
        camera_map: dict[str, str] | None = None,
    ):
        self.root = Path(root)
        self.info = self._read_info()
        self._tasks = self._read_tasks()
        self._episodes = self._read_episodes()
        self._by_index = {int(_scalar(row["episode_index"])): row for row in self._episodes}
        self._data_cache: dict[Path, Any] = {}
        self.video_keys = tuple(k for k, f in self._features().items() if f.get("dtype") == "video")
        # Overrides add to the default map rather than replacing it, so a caller
        # can rename one camera without dropping the others.
        self.camera_map = {**self._default_camera_map(), **(camera_map or {})}
        self._offsets = self._frame_offsets()

    # ------------------------------------------------------------------ probe

    def _features(self) -> dict[str, Any]:
        return self.info_features

    def _read_info(self) -> DatasetInfo:
        import json

        path = self.root / "meta/info.json"
        if not path.exists():
            raise FileNotFoundError(f"not a LeRobot v3 dataset (no meta/info.json): {self.root}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        version = str(raw.get("codebase_version", ""))
        if not version.startswith("v3"):
            raise ValueError(
                f"LeRobot codebase_version {version!r} is not supported; this adapter reads v3.x only."
            )
        self.info_features = raw.get("features", {})
        self._raw_info = raw
        return DatasetInfo(
            kind=self.kind,
            root=self.root,
            fps=float(raw["fps"]),
            robot_type=str(raw.get("robot_type", "")),
            total_episodes=int(raw.get("total_episodes", 0)),
            total_frames=int(raw.get("total_frames", 0)),
            video_keys=tuple(k for k, f in self.info_features.items() if f.get("dtype") == "video"),
        )

    def _read_tasks(self) -> dict[int, str]:
        table = pq.read_table(self.root / "meta/tasks.parquet")
        column = "task" if "task" in table.column_names else table.column_names[-1]
        return {
            int(row["task_index"]): str(row[column])
            for row in table.select(["task_index", column]).to_pylist()
        }

    def _read_episodes(self) -> list[dict]:
        rows: list[dict] = []
        for path in sorted(self.root.glob("meta/episodes/**/*.parquet")):
            rows.extend(pq.read_table(path).to_pylist())
        if not rows:
            raise FileNotFoundError(f"no episode metadata under {self.root / 'meta/episodes'}")
        rows.sort(key=lambda row: int(_scalar(row["episode_index"])))
        return rows

    def _default_camera_map(self) -> dict[str, str]:
        """``observation.images.wrist`` -> ``wrist``; the last token names the camera."""
        mapping: dict[str, str] = {}
        for key in self.video_keys:
            mapping.setdefault(key.rsplit(".", 1)[-1], key)
        return mapping

    def _frame_offsets(self) -> dict[tuple[str, int], int]:
        """Cumulative frame start per ``(video_key, episode_index)`` within its shard."""
        offsets: dict[tuple[str, int], int] = {}
        for video_key in self.video_keys:
            running: dict[tuple[int, int], int] = {}
            for row in self._episodes:
                shard = (
                    int(_scalar(row[f"videos/{video_key}/chunk_index"])),
                    int(_scalar(row[f"videos/{video_key}/file_index"])),
                )
                index = int(_scalar(row["episode_index"]))
                offsets[(video_key, index)] = running.get(shard, 0)
                running[shard] = running.get(shard, 0) + int(_scalar(row["length"]))
        return offsets

    # --------------------------------------------------------------- episodes

    def episode_indices(self) -> list[int]:
        return sorted(self._by_index)

    def episode_meta(self, index: int) -> EpisodeMeta:
        row = self._by_index.get(int(index))
        if row is None:
            raise KeyError(f"episode {index} not present in this dataset")
        length = int(_scalar(row["length"]))
        task_list = row.get("tasks") or []
        task = str(task_list[0]) if task_list else self._tasks.get(int(_scalar(row.get("task_index", 0))), "")
        videos: dict[str, VideoSegment] = {}
        video_info = {k: self.info_features[k] for k in self.video_keys}
        for camera_name, video_key in self.camera_map.items():
            if video_key not in video_info:
                raise KeyError(
                    f"camera {camera_name!r} maps to {video_key!r}, which is not a video feature. "
                    f"Available: {sorted(self.video_keys)}"
                )
            chunk = int(_scalar(row[f"videos/{video_key}/chunk_index"]))
            file_index = int(_scalar(row[f"videos/{video_key}/file_index"]))
            path = self.root / self._raw_info["video_path"].format(
                video_key=video_key, chunk_index=chunk, file_index=file_index
            )
            shape = video_info[video_key].get("shape") or [0, 0, 0]
            height, width = int(shape[0]), int(shape[1]) if len(shape) > 1 else 0
            videos[camera_name] = VideoSegment(
                path=path,
                start_frame=self._offsets[(video_key, int(index))],
                frame_count=length,
                fps=self.info.fps,
                width=width,
                height=height,
            )
        return EpisodeMeta(index=int(index), length=length, task=task, videos=videos)

    def read_episode(self, index: int) -> SourceEpisode:
        meta = self.episode_meta(index)
        row = self._by_index[int(index)]
        start = int(_scalar(row["dataset_from_index"]))
        stop = int(_scalar(row["dataset_to_index"]))
        chunk = int(_scalar(row["data/chunk_index"]))
        file_index = int(_scalar(row["data/file_index"]))
        table = self._data_table(chunk, file_index)

        if "index" in table.column_names:
            mask = [start <= int(v) < stop for v in table.column("index").to_pylist()]
            table = table.filter(mask)
        elif "episode_index" in table.column_names:
            table = table.filter(
                [int(v) == int(index) for v in table.column("episode_index").to_pylist()]
            )
            if "frame_index" in table.column_names:
                order = np.argsort(np.asarray(table.column("frame_index").to_pylist()))
                table = table.take(order.tolist())
        if table.num_rows != meta.length:
            raise ValueError(
                f"episode {index}: metadata length {meta.length} but {table.num_rows} rows "
                f"at dataset indices [{start}, {stop})"
            )

        state = _column_to_2d(table, "observation.state")
        action = _column_to_2d(table, "action")
        if not np.isfinite(state).all() or not np.isfinite(action).all():
            raise ValueError(f"episode {index}: non-finite state or action values")
        names = _joint_names(self.info_features, "observation.state")
        if state.shape[1] != len(names):
            raise ValueError(
                f"episode {index}: state width {state.shape[1]} does not match {len(names)} joint names"
            )
        return SourceEpisode(meta=meta, state=state, action=action, joint_names=tuple(names))

    def _data_table(self, chunk: int, file_index: int):
        template = self._raw_info["data_path"]
        path = self.root / template.format(chunk_index=chunk, file_index=file_index)
        if path not in self._data_cache:
            if not path.exists():
                raise FileNotFoundError(f"missing frame table {path}")
            self._data_cache[path] = pq.read_table(path)
        return self._data_cache[path]

    # ------------------------------------------------------------------ probe

    def probe(self) -> dict:
        first = self._episodes[0]
        return {
            "kind": self.kind,
            "root": str(self.root),
            "codebase_version": self._raw_info.get("codebase_version"),
            "fps": self.info.fps,
            "robot_type": self.info.robot_type,
            "total_episodes": self.info.total_episodes,
            "total_frames": self.info.total_frames,
            "video_keys": list(self.video_keys),
            "camera_map": dict(self.camera_map),
            "joint_names": _joint_names(self.info_features, "observation.state"),
            "tasks": self._tasks,
            "episodes_columns": sorted(k for k in first if not k.startswith("stats/")),
            "first_episode": {k: _plain(first[k]) for k in first if not k.startswith("stats/")},
        }

    def close(self) -> None:  # nothing is held open
        self._data_cache.clear()


# ---------------------------------------------------------------- helpers


def _scalar(value: Any) -> Any:
    """LeRobot writes some metadata as one-element lists."""
    if isinstance(value, (list, tuple, np.ndarray)) and len(value) == 1:
        return value[0]
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _joint_names(features: dict[str, Any], key: str) -> list[str]:
    names = (features.get(key) or {}).get("names") or []
    cleaned = []
    for name in names:
        name = str(name)
        cleaned.append(name[: -len(".pos")] if name.endswith(".pos") else name)
    if not cleaned:
        raise ValueError(f"feature {key!r} has no joint names in meta/info.json")
    if not any(hint in cleaned[-1].lower() for hint in GRIPPER_HINTS):
        raise ValueError(
            f"expected the gripper last in {key!r} joint names, got {cleaned[-1]!r}. "
            "This adapter assumes LeRobot's [arm..., gripper] order."
        )
    return cleaned


def _column_to_2d(table, name: str) -> np.ndarray:
    if name not in table.column_names:
        raise KeyError(f"frame table has no {name!r} column: {table.column_names}")
    rows = table.column(name).to_pylist()
    if not rows:
        raise ValueError(f"column {name!r} is empty")
    widths = {len(row) if isinstance(row, (list, tuple)) else 1 for row in rows}
    if len(widths) != 1:
        raise ValueError(f"column {name!r} has ragged widths {sorted(widths)}")
    return np.asarray(rows, dtype=np.float64)
