"""The interface every source format implements.

Adding ROS bag, RLDS or custom HDF5 support means adding one module here; the
conversion core, annotations, video pipeline and CLI stay unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class VideoSegment:
    """One contiguous run of frames inside a source video file."""

    path: Path
    start_frame: int
    frame_count: int
    fps: float
    width: int
    height: int


@dataclass(frozen=True)
class EpisodeMeta:
    index: int
    length: int
    task: str
    videos: dict[str, VideoSegment]  # keyed by the TARGET camera name


@dataclass(frozen=True)
class SourceEpisode:
    meta: EpisodeMeta
    state: np.ndarray   # (T, D) float64, gripper last
    action: np.ndarray  # (T, D) float64, gripper last
    joint_names: tuple[str, ...]  # includes the gripper as the last entry


@dataclass(frozen=True)
class DatasetInfo:
    kind: str
    root: Path
    fps: float
    robot_type: str
    total_episodes: int
    total_frames: int
    video_keys: tuple[str, ...]


class DatasetSource(Protocol):
    info: DatasetInfo

    def episode_indices(self) -> list[int]: ...

    def episode_meta(self, index: int) -> EpisodeMeta: ...

    def read_episode(self, index: int) -> SourceEpisode: ...

    def probe(self) -> dict: ...

    def close(self) -> None: ...
