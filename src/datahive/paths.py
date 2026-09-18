"""Path resolution helpers for the samples/ tree.

Everything lives under a single ``samples/`` directory:

    samples/
        robot_profile.yaml
        attachments.yaml            (optional local override)
        .datahive/index.db
        {session_id}/
            setup.jpg
            trials.csv
            episodes/
                {episode_id}.h5
                {episode_id}_cam_external.mp4
                {episode_id}_cam_wrist.mp4   (optional)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SAMPLES_DIRNAME = "samples"
INDEX_DIRNAME = ".datahive"
INDEX_FILENAME = "index.db"
PROFILE_FILENAME = "robot_profile.yaml"
ATTACHMENTS_FILENAME = "attachments.yaml"


def default_samples_root(cwd: Path | None = None) -> Path:
    """Resolve the samples/ root: cwd/samples if it exists, else cwd itself
    when cwd is already named samples, else cwd/samples (to be created)."""
    cwd = (cwd or Path.cwd()).resolve()
    if cwd.name == DEFAULT_SAMPLES_DIRNAME:
        return cwd
    candidate = cwd / DEFAULT_SAMPLES_DIRNAME
    return candidate


def index_db_path(samples_root: Path) -> Path:
    return samples_root / INDEX_DIRNAME / INDEX_FILENAME


def profile_path(samples_root: Path) -> Path:
    return samples_root / PROFILE_FILENAME


def attachments_override_path(samples_root: Path) -> Path:
    return samples_root / ATTACHMENTS_FILENAME


def session_dir(samples_root: Path, session_id: str) -> Path:
    return samples_root / session_id


def episodes_dir(samples_root: Path, session_id: str) -> Path:
    return session_dir(samples_root, session_id) / "episodes"


def trials_csv_path(samples_root: Path, session_id: str) -> Path:
    return session_dir(samples_root, session_id) / "trials.csv"


def setup_image_path(samples_root: Path, session_id: str) -> Path:
    return session_dir(samples_root, session_id) / "setup.jpg"


@dataclass
class EpisodePaths:
    samples_root: Path
    session_id: str
    episode_id: str
    session_dir: Path
    h5: Path
    trials_csv: Path
    setup_jpg: Path
    videos: dict[str, Path] = field(default_factory=dict)

    @property
    def episodes_dir(self) -> Path:
        return self.h5.parent


def find_episode_session(samples_root: Path, episode_id: str) -> str | None:
    """Search samples/*/episodes/{episode_id}.h5 for the owning session."""
    if not samples_root.is_dir():
        return None
    for child in samples_root.iterdir():
        if not child.is_dir() or child.name.startswith("."):
            continue
        candidate = child / "episodes" / f"{episode_id}.h5"
        if candidate.is_file():
            return child.name
    return None


def resolve_episode_paths(
    samples_root: Path, episode_id: str, session_id: str | None = None
) -> EpisodePaths:
    from datahive.errors import EpisodeNotFound

    if session_id is None:
        session_id = find_episode_session(samples_root, episode_id)
        if session_id is None:
            raise EpisodeNotFound(
                f"No episode '{episode_id}' found under {samples_root}"
            )
    sdir = session_dir(samples_root, session_id)
    edir = episodes_dir(samples_root, session_id)
    h5 = edir / f"{episode_id}.h5"
    if not h5.is_file():
        raise EpisodeNotFound(f"Episode file not found: {h5}")
    videos: dict[str, Path] = {}
    if edir.is_dir():
        for f in edir.glob(f"{episode_id}_cam_*.mp4"):
            camera_name = f.stem[len(f"{episode_id}_cam_") :]
            videos[camera_name] = f
    return EpisodePaths(
        samples_root=samples_root,
        session_id=session_id,
        episode_id=episode_id,
        session_dir=sdir,
        h5=h5,
        trials_csv=trials_csv_path(samples_root, session_id),
        setup_jpg=setup_image_path(samples_root, session_id),
        videos=videos,
    )


def iter_all_episode_ids(samples_root: Path) -> list[tuple[str, str]]:
    """Return [(session_id, episode_id), ...] for every episode on disk."""
    out: list[tuple[str, str]] = []
    if not samples_root.is_dir():
        return out
    for child in sorted(samples_root.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        edir = child / "episodes"
        if not edir.is_dir():
            continue
        for h5 in sorted(edir.glob("*.h5")):
            out.append((child.name, h5.stem))
    return out
