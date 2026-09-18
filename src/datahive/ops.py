"""Shared operations used by BOTH the CLI and the GUI backend: upload,
sync, delete, list. Neither front end reimplements this logic -- they only
marshal arguments and format output.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from datahive.config import Config, load_config
from datahive.episode import read_header
from datahive.errors import DatahiveError, EpisodeNotFound, HubError
from datahive.hub import Hub, remote_paths, remote_setup_jpg_path, remote_trials_csv_path
from datahive.index import EpisodeRecord, Index
from datahive.paths import resolve_episode_paths, trials_csv_path
from datahive.trials import get_row, merge_rows, read_rows, upsert_row


def _get_hub(hub: Hub | None, cfg: Config | None = None) -> Hub:
    if hub is not None:
        return hub
    cfg = cfg or load_config()
    return Hub(cfg)


@dataclass
class UploadResult:
    episode_id: str
    uploaded: bool
    skipped_reason: str | None = None
    remote_paths: list[str] = field(default_factory=list)
    error: str | None = None


def upload_episode(
    samples_root: Path, episode_id: str, *, hub: Hub | None = None, force: bool = False
) -> UploadResult:
    with Index(samples_root) as idx:
        if idx.get(episode_id) is None:
            idx.scan()
        rec = idx.get(episode_id)
        if rec is None:
            raise EpisodeNotFound(episode_id)

        current_hash = idx.refresh_hash(episode_id)
        rec = idx.get(episode_id)

        if not force and rec.status not in ("validated", "uploaded", "upload_failed"):
            return UploadResult(
                episode_id, uploaded=False, skipped_reason=f"status is '{rec.status}', not validated"
            )
        if not force and rec.status == "uploaded" and current_hash == rec.uploaded_hash:
            return UploadResult(episode_id, uploaded=False, skipped_reason="already uploaded, unchanged")

        paths = resolve_episode_paths(samples_root, episode_id, rec.session_id)
        header = read_header(paths.h5)
        hub_client = _get_hub(hub)

        files = remote_paths(header, paths)
        try:
            # Merge trials.csv with the remote copy so two machines
            # uploading different episodes of the same session don't
            # clobber each other's rows (local wins on conflict).
            local_rows = read_rows(paths.trials_csv)
            remote_text = hub_client.download_text(remote_trials_csv_path(rec.session_id))
            if remote_text:
                import csv
                import io

                remote_rows = list(csv.DictReader(io.StringIO(remote_text)))
                merged = merge_rows(local_rows, remote_rows)
                from datahive.schema import TRIAL_COLUMNS

                out = io.StringIO()
                writer = csv.DictWriter(out, fieldnames=list(TRIAL_COLUMNS))
                writer.writeheader()
                for row in merged:
                    writer.writerow({c: row.get(c, "") for c in TRIAL_COLUMNS})
                hub_client.upload_text(
                    remote_trials_csv_path(rec.session_id),
                    out.getvalue(),
                    commit_message=f"Merge trials.csv for {rec.session_id}",
                )
            elif paths.trials_csv.exists():
                hub_client.upload_text(
                    remote_trials_csv_path(rec.session_id),
                    paths.trials_csv.read_text(encoding="utf-8"),
                    commit_message=f"Upload trials.csv for {rec.session_id}",
                )

            if paths.setup_jpg.exists():
                files[remote_setup_jpg_path(rec.session_id)] = paths.setup_jpg

            hub_client.upload_episode(files, commit_message=f"Upload episode {episode_id}")
        except HubError as e:
            idx.set_status(episode_id, "upload_failed", error=str(e))
            return UploadResult(episode_id, uploaded=False, error=str(e))

        idx.mark_uploaded(episode_id, current_hash)
        return UploadResult(episode_id, uploaded=True, remote_paths=list(files.keys()))


def bulk_upload_episodes(
    samples_root: Path, episode_ids: list[str], *, hub: Hub | None = None, force: bool = False
) -> list[UploadResult]:
    """Uploads several episodes, reusing one Hub client across all of them.
    A bad episode_id -- or a missing/invalid Hub config -- doesn't abort the
    batch: every id comes back as a failed UploadResult with the error
    message, same shape as a successful one."""
    try:
        hub_client = _get_hub(hub)
    except DatahiveError as e:
        return [UploadResult(episode_id, uploaded=False, error=str(e)) for episode_id in episode_ids]

    results: list[UploadResult] = []
    for episode_id in episode_ids:
        try:
            results.append(upload_episode(samples_root, episode_id, hub=hub_client, force=force))
        except DatahiveError as e:
            results.append(UploadResult(episode_id, uploaded=False, error=str(e)))
    return results


def bulk_delete_episodes(
    samples_root: Path, episode_ids: list[str], *, hub: Hub | None = None, delete_remote: bool = True
) -> list[DeleteResult]:
    """Deletes several episodes. Each call resolves its own Hub client
    lazily (only if that particular episode was actually uploaded), same as
    a single delete_episode -- so deleting a batch of never-uploaded
    episodes never requires a Hub config at all."""
    results: list[DeleteResult] = []
    for episode_id in episode_ids:
        try:
            results.append(delete_episode(samples_root, episode_id, hub=hub, delete_remote=delete_remote))
        except DatahiveError as e:
            results.append(DeleteResult(episode_id, deleted_local=False, deleted_remote=False, error=str(e)))
    return results


@dataclass
class SyncReport:
    newly_recorded: list[str] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    upload_failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    remote_only: list[str] = field(default_factory=list)


def sync(samples_root: Path, *, hub: Hub | None = None, dry_run: bool = False) -> SyncReport:
    report = SyncReport()
    with Index(samples_root) as idx:
        report.newly_recorded = idx.scan()

        # Recompute content_hash for everything so a re-annotation or a
        # touched file is detected even if nothing has uploaded since.
        for rec in idx.all():
            try:
                idx.refresh_hash(rec.episode_id)
            except Exception:
                pass

        candidates = [r for r in idx.all() if r.needs_upload]

    hub_client = _get_hub(hub)

    if not dry_run:
        for rec in candidates:
            result = upload_episode(samples_root, rec.episode_id, hub=hub_client)
            if result.uploaded:
                report.uploaded.append(rec.episode_id)
            elif result.error:
                report.upload_failed.append(rec.episode_id)
            else:
                report.skipped.append(rec.episode_id)
    else:
        report.skipped = [r.episode_id for r in candidates]

    remote_files = hub_client.list_files()
    remote_episode_ids: set[str] = set()
    for path in remote_files:
        name = Path(path).name
        if name.endswith(".h5"):
            remote_episode_ids.add(Path(name).stem)

    with Index(samples_root) as idx:
        known_ids = {r.episode_id for r in idx.all()}
    report.remote_only = sorted(remote_episode_ids - known_ids)

    return report


@dataclass
class DeleteResult:
    episode_id: str
    deleted_local: bool
    deleted_remote: bool
    error: str | None = None


def delete_episode(
    samples_root: Path,
    episode_id: str,
    *,
    hub: Hub | None = None,
    delete_remote: bool = True,
) -> DeleteResult:
    with Index(samples_root) as idx:
        rec = idx.get(episode_id)
        if rec is None:
            idx.scan()
            rec = idx.get(episode_id)
        if rec is None:
            raise EpisodeNotFound(episode_id)

        paths = resolve_episode_paths(samples_root, episode_id, rec.session_id)
        was_uploaded = rec.status == "uploaded"

        deleted_remote = False
        if delete_remote and was_uploaded:
            hub_client = _get_hub(hub)
            header = read_header(paths.h5)
            remote_file_map = remote_paths(header, paths)
            hub_client.delete_paths(
                list(remote_file_map.keys()), commit_message=f"Delete episode {episode_id}"
            )
            deleted_remote = True

        if paths.h5.exists():
            paths.h5.unlink()
        for v in paths.videos.values():
            if v.exists():
                v.unlink()

        idx.remove(episode_id)

    return DeleteResult(episode_id, deleted_local=True, deleted_remote=deleted_remote)


@dataclass
class EpisodeStatus:
    episode_id: str
    session_id: str
    trial_id: str | None
    status: str
    last_error: str | None
    last_synced_at: str | None
    created_at: str | None = None
    n_steps: int | None = None
    duration_s: float | None = None
    remote: bool | None = None


def list_episodes(
    samples_root: Path,
    *,
    hub: Hub | None = None,
    include_remote: bool = False,
    status: str | None = None,
    query: str | None = None,
) -> list[EpisodeStatus]:
    with Index(samples_root) as idx:
        idx.scan()
        records = idx.all(status=status, query=query)

    remote_ids: set[str] | None = None
    if include_remote:
        hub_client = _get_hub(hub)
        remote_ids = {
            Path(p).stem for p in hub_client.list_files() if p.endswith(".h5")
        }

    out = []
    for r in records:
        out.append(
            EpisodeStatus(
                episode_id=r.episode_id,
                session_id=r.session_id,
                trial_id=r.trial_id,
                status=r.effective_status,
                last_error=r.last_error,
                last_synced_at=r.last_synced_at,
                created_at=r.created_at,
                n_steps=r.n_steps,
                duration_s=r.duration_s,
                remote=(r.episode_id in remote_ids) if remote_ids is not None else None,
            )
        )
    return out


@dataclass
class SyncStatus:
    """A cheap summary for a "connected to the Hub?" indicator -- doesn't
    scan remote file lists, just whether the config exists, the Hub
    answers, and how many local episodes still need to go up."""

    configured: bool
    connected: bool
    repo_id: str | None = None
    error: str | None = None
    pending_count: int = 0
    uploaded_count: int = 0
    total_count: int = 0


def get_sync_status(samples_root: Path, *, hub: Hub | None = None) -> SyncStatus:
    try:
        cfg = load_config()
    except DatahiveError:
        return SyncStatus(configured=False, connected=False)

    with Index(samples_root) as idx:
        idx.scan()
        records = idx.all()
    pending = sum(1 for r in records if r.needs_upload)
    uploaded = sum(1 for r in records if r.status == "uploaded" and r.content_hash == r.uploaded_hash)

    try:
        hub_client = _get_hub(hub, cfg)
        hub_client.whoami()  # cheap, read-only reachability check
        return SyncStatus(
            configured=True, connected=True, repo_id=cfg.repo_id,
            pending_count=pending, uploaded_count=uploaded, total_count=len(records),
        )
    except DatahiveError as e:
        return SyncStatus(
            configured=True, connected=False, repo_id=cfg.repo_id, error=str(e),
            pending_count=pending, uploaded_count=uploaded, total_count=len(records),
        )


def list_recent_annotations(
    samples_root: Path, *, exclude_episode_id: str | None = None, limit: int = 20
) -> list[dict]:
    """The most recently saved trial annotations anywhere under samples/,
    newest first, for the GUI's "Copy from previous" picker -- lets an
    annotator pick one to fill a fresh episode from instead of retyping
    everything. Skips episodes with no saved annotation row and (by
    default) the episode being annotated right now."""
    with Index(samples_root) as idx:
        idx.scan()
        records = idx.all()
    results: list[dict] = []
    for rec in sorted(records, key=lambda r: r.updated_at, reverse=True):
        if rec.episode_id == exclude_episode_id or not rec.trial_id:
            continue
        row = get_row(trials_csv_path(samples_root, rec.session_id), rec.trial_id)
        if row:
            results.append({"episode_id": rec.episode_id, "session_id": rec.session_id, "annotation": row})
        if len(results) >= limit:
            break
    return results
