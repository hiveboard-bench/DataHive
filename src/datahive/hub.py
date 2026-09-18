"""Thin wrapper around huggingface_hub.HfApi, plus remote path mapping.

Every real network call goes through this module so tests can inject a
fake `api` object and assert on exactly what would have been sent, without
ever touching the real Hub.
"""

from __future__ import annotations

from pathlib import Path

from huggingface_hub import HfApi
from huggingface_hub.utils import HfHubHTTPError

from datahive.config import Config
from datahive.errors import HubError
from datahive.paths import EpisodePaths
from datahive.schema import EpisodeHeader

REPO_TYPE = "dataset"


def remote_paths(header: EpisodeHeader, paths: EpisodePaths) -> dict[str, Path]:
    """Map of {remote_path: local_path} for one episode's files (mirrors
    the samples/ layout exactly, minus .datahive/)."""
    out: dict[str, Path] = {
        f"{paths.session_id}/episodes/{paths.episode_id}.h5": paths.h5,
    }
    for camera_name, local_path in sorted(paths.videos.items()):
        if local_path.exists():
            out[f"{paths.session_id}/episodes/{local_path.name}"] = local_path
    return out


def remote_trials_csv_path(session_id: str) -> str:
    return f"{session_id}/trials.csv"


def remote_setup_jpg_path(session_id: str) -> str:
    return f"{session_id}/setup.jpg"


class Hub:
    def __init__(self, cfg: Config, api: HfApi | None = None):
        self.cfg = cfg
        self.api = api or HfApi(token=cfg.hf_token, endpoint=cfg.endpoint)

    def whoami(self) -> dict:
        try:
            return self.api.whoami(token=self.cfg.hf_token)
        except Exception as e:
            raise HubError(f"Could not verify Hugging Face token: {e}") from e

    def upload_episode(self, files: dict[str, Path], *, commit_message: str) -> str:
        try:
            last = None
            for remote_path, local_path in files.items():
                last = self.api.upload_file(
                    path_or_fileobj=str(local_path),
                    path_in_repo=remote_path,
                    repo_id=self.cfg.repo_id,
                    repo_type=REPO_TYPE,
                    commit_message=commit_message,
                )
            return str(last) if last is not None else ""
        except HfHubHTTPError as e:
            raise HubError(f"Upload failed: {e}") from e

    def upload_text(self, remote_path: str, content: str, *, commit_message: str) -> str:
        try:
            return str(
                self.api.upload_file(
                    path_or_fileobj=content.encode("utf-8"),
                    path_in_repo=remote_path,
                    repo_id=self.cfg.repo_id,
                    repo_type=REPO_TYPE,
                    commit_message=commit_message,
                )
            )
        except HfHubHTTPError as e:
            raise HubError(f"Upload failed: {e}") from e

    def download_text(self, remote_path: str) -> str | None:
        try:
            from huggingface_hub import hf_hub_download

            local = hf_hub_download(
                repo_id=self.cfg.repo_id,
                repo_type=REPO_TYPE,
                filename=remote_path,
                token=self.cfg.hf_token,
            )
            return Path(local).read_text(encoding="utf-8")
        except Exception:
            return None

    def delete_paths(self, paths: list[str], *, commit_message: str) -> str:
        if not paths:
            return ""
        try:
            from huggingface_hub import CommitOperationDelete

            ops = [CommitOperationDelete(path_in_repo=p) for p in paths]
            info = self.api.create_commit(
                repo_id=self.cfg.repo_id,
                repo_type=REPO_TYPE,
                operations=ops,
                commit_message=commit_message,
            )
            return str(info)
        except HfHubHTTPError as e:
            raise HubError(f"Delete failed: {e}") from e

    def list_files(self) -> list[str]:
        try:
            return list(self.api.list_repo_files(repo_id=self.cfg.repo_id, repo_type=REPO_TYPE))
        except HfHubHTTPError as e:
            raise HubError(f"Could not list repo files: {e}") from e
