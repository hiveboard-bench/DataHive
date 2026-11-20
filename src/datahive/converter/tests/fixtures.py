"""Synthetic LeRobot v3.0 datasets for the unit tests.

Everything is generated into a temporary directory: the Parquet tables with
pyarrow, and tiny H.264 clips with the ffmpeg that imageio-ffmpeg ships. The
clips are 320x240 (above the validator's 180 px minimum) so converted output
can be validated by ``datahive`` itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ..video import find_ffmpeg

JOINT_NAMES = [
    "shoulder_pan.pos",
    "shoulder_lift.pos",
    "elbow_flex.pos",
    "wrist_flex.pos",
    "wrist_roll.pos",
    "gripper.pos",
]
VIDEO_KEYS = ("observation.images.wrist", "observation.images.external")
FPS = 25
WIDTH, HEIGHT = 320, 240


def make_frame_table(episode_lengths: list[int], fps: int = FPS) -> list[dict]:
    rows: list[dict] = []
    index = 0
    for episode_index, length in enumerate(episode_lengths):
        for frame_index in range(length):
            phase = frame_index / max(1, length - 1)
            state = [10.0 * np.sin(phase * np.pi) + j for j in range(6)]
            action = [value + 0.5 for value in state]
            rows.append(
                {
                    "action": [float(v) for v in action],
                    "observation.state": [float(v) for v in state],
                    "timestamp": frame_index / fps,
                    "frame_index": frame_index,
                    "episode_index": episode_index,
                    "index": index,
                    "task_index": 0,
                }
            )
            index += 1
    return rows


def make_dataset(
    root: Path,
    *,
    episode_lengths: tuple[int, ...] = (40, 30),
    camera_keys: tuple[str, ...] = VIDEO_KEYS,
    fps: int = FPS,
) -> Path:
    root = Path(root)
    (root / "meta/episodes/chunk-000").mkdir(parents=True, exist_ok=True)
    (root / "data/chunk-000").mkdir(parents=True, exist_ok=True)

    features = {
        "action": {"dtype": "float32", "names": JOINT_NAMES, "shape": [6]},
        "observation.state": {"dtype": "float32", "names": JOINT_NAMES, "shape": [6]},
        "timestamp": {"dtype": "float32", "shape": [1], "names": None},
        "frame_index": {"dtype": "int64", "shape": [1], "names": None},
        "episode_index": {"dtype": "int64", "shape": [1], "names": None},
        "index": {"dtype": "int64", "shape": [1], "names": None},
        "task_index": {"dtype": "int64", "shape": [1], "names": None},
    }
    for key in camera_keys:
        features[key] = {
            "dtype": "video",
            "shape": [HEIGHT, WIDTH, 3],
            "names": ["height", "width", "channels"],
            "info": {"video.codec": "h264", "video.fps": fps},
        }
    info = {
        "codebase_version": "v3.0",
        "fps": fps,
        "total_episodes": len(episode_lengths),
        "total_frames": int(sum(episode_lengths)),
        "total_tasks": 1,
        "chunks_size": 1000,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
        "robot_type": "so_follower",
        "features": features,
    }
    (root / "meta/info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")

    pq.write_table(
        pa.table({"task_index": [0], "task": ["Open the cover and press the button"]}),
        root / "meta/tasks.parquet",
    )

    frames = make_frame_table(list(episode_lengths), fps)
    pq.write_table(_rows_to_table(frames), root / "data/chunk-000/file-000.parquet")

    episode_rows: list[dict] = []
    offset = 0
    for episode_index, length in enumerate(episode_lengths):
        row = {
            "episode_index": episode_index,
            "tasks": ["Open the cover and press the button"],
            "length": length,
            "data/chunk_index": 0,
            "data/file_index": 0,
            "dataset_from_index": offset,
            "dataset_to_index": offset + length,
        }
        for key in camera_keys:
            row[f"videos/{key}/chunk_index"] = 0
            row[f"videos/{key}/file_index"] = 0
            row[f"videos/{key}/from_timestamp"] = offset / fps
            row[f"videos/{key}/to_timestamp"] = (offset + length) / fps
        episode_rows.append(row)
        offset += length
    pq.write_table(pa.Table.from_pylist(episode_rows), root / "meta/episodes/chunk-000/file-000.parquet")

    total = sum(episode_lengths)
    for key in camera_keys:
        dest = root / f"videos/{key}/chunk-000/file-000.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _make_video(dest, frames=total, fps=fps)
    return root


def _rows_to_table(rows: list[dict]) -> pa.Table:
    return pa.table(
        {
            "action": pa.array([r["action"] for r in rows], type=pa.list_(pa.float32())),
            "observation.state": pa.array([r["observation.state"] for r in rows], type=pa.list_(pa.float32())),
            "timestamp": pa.array([r["timestamp"] for r in rows], type=pa.float32()),
            "frame_index": pa.array([r["frame_index"] for r in rows], type=pa.int64()),
            "episode_index": pa.array([r["episode_index"] for r in rows], type=pa.int64()),
            "index": pa.array([r["index"] for r in rows], type=pa.int64()),
            "task_index": pa.array([r["task_index"] for r in rows], type=pa.int64()),
        }
    )


def _make_video(dest: Path, *, frames: int, fps: int) -> None:
    cmd = [
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={WIDTH}x{HEIGHT}:rate={fps}",
        "-frames:v", str(frames), "-c:v", "libx264", "-preset", "ultrafast",
        "-pix_fmt", "yuv420p", str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"fixture video failed: {proc.stderr[-800:]}")


def annotation_csv(path: Path, entries: list[dict]) -> Path:
    import csv

    fieldnames = ["episode_index", "outcome", "strategy", "stage_reached", "failure_cause",
                  "failure_cause_detail", "severity", "n_attempts", "n_regrasps", "notes"]
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for entry in entries:
            writer.writerow({name: entry.get(name, "") for name in fieldnames})
    return Path(path)
