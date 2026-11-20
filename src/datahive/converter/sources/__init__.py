"""Source registry: turn a path or an HF repo id into a ``DatasetSource``."""

from __future__ import annotations

import json
from pathlib import Path

from .base import DatasetInfo, DatasetSource, EpisodeMeta, SourceEpisode, VideoSegment
from .lerobot_v3 import LeRobotV3Source

ADAPTERS = {"lerobot_v3": LeRobotV3Source}


def resolve_source_path(source: str, *, cache_dir: Path, metadata_only: bool = False) -> Path:
    path = Path(source)
    if path.exists():
        return path
    if "/" not in source:
        raise FileNotFoundError(
            f"{source!r} is neither an existing directory nor a Hugging Face repo id (owner/name)."
        )
    from huggingface_hub import snapshot_download

    patterns = ["meta/**", "data/**"] if metadata_only else None
    return Path(
        snapshot_download(
            source,
            repo_type="dataset",
            local_dir=cache_dir / source.replace("/", "__"),
            allow_patterns=patterns,
        )
    )


def detect_kind(root: Path) -> str:
    info = Path(root) / "meta/info.json"
    if not info.exists():
        raise FileNotFoundError(
            f"{root} does not look like a supported dataset: no meta/info.json. "
            "Supported sources: LeRobot Dataset v3.x."
        )
    version = str(json.loads(info.read_text(encoding="utf-8")).get("codebase_version", ""))
    if version.startswith("v3"):
        return "lerobot_v3"
    raise ValueError(
        f"LeRobot codebase_version {version!r} is not supported. "
        "Only v3.x datasets can be converted."
    )


def open_source(
    source: str,
    *,
    cache_dir: Path,
    metadata_only: bool = False,
    camera_map: dict[str, str] | None = None,
) -> DatasetSource:
    root = resolve_source_path(source, cache_dir=cache_dir, metadata_only=metadata_only)
    kind = detect_kind(root)
    return ADAPTERS[kind](root, camera_map=camera_map)


__all__ = [
    "ADAPTERS",
    "DatasetInfo",
    "DatasetSource",
    "EpisodeMeta",
    "SourceEpisode",
    "VideoSegment",
    "detect_kind",
    "open_source",
    "resolve_source_path",
]
