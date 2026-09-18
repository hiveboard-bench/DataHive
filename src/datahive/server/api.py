"""JSON API routes. Every route calls the same ops/annotate/validate
functions the CLI calls -- no reimplementation of business logic here."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from datahive import ops
from datahive.attachments import load_registry
from datahive.episode import read_header, read_trajectory
from datahive.errors import DatahiveError
from datahive.index import Index
from datahive.paths import resolve_episode_paths
from datahive.profile import load_profile
from datahive.trials import get_row


class ValidatePayload(BaseModel):
    attachment_id: Optional[str] = None
    outcome: Optional[str] = None
    failure_cause: Optional[str] = None
    completion_time_s: Optional[float] = None
    n_attempts: Optional[int] = None
    n_regrasps: Optional[int] = None
    stage_reached: Optional[int] = None
    strategy: Optional[str] = None
    notes: str = ""


def build_router(samples_root: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/api/profile")
    def get_profile():
        try:
            profile = load_profile(samples_root, allow_incomplete=True)
        except DatahiveError as e:
            raise HTTPException(404, str(e))
        return profile.to_dict()

    @router.get("/api/attachments")
    def get_attachments():
        registry = load_registry(samples_root)
        return {aid: vars(info) for aid, info in registry.items()}

    @router.get("/api/episodes")
    def list_episodes(q: Optional[str] = None, status: Optional[str] = None):
        try:
            episodes = ops.list_episodes(samples_root, status=status, query=q)
        except DatahiveError as e:
            raise HTTPException(400, str(e))
        return [e.__dict__ for e in episodes]

    @router.get("/api/episodes/{episode_id}")
    def get_episode(episode_id: str):
        try:
            paths = resolve_episode_paths(samples_root, episode_id)
            header = read_header(paths.h5)
        except DatahiveError as e:
            raise HTTPException(404, str(e))

        with Index(samples_root) as idx:
            rec = idx.get(episode_id)

        row = get_row(paths.trials_csv, header.trial_id)
        return {
            "header": header.model_dump(mode="json"),
            "annotation": row,
            "index": vars(rec) if rec else None,
            "cameras": sorted(paths.videos.keys()),
            "has_setup_image": paths.setup_jpg.exists(),
        }

    @router.get("/api/episodes/{episode_id}/trajectory")
    def get_trajectory(episode_id: str, group: str = "proprioception", max_points: int = Query(2000, le=50000)):
        try:
            paths = resolve_episode_paths(samples_root, episode_id)
        except DatahiveError as e:
            raise HTTPException(404, str(e))
        return read_trajectory(paths.h5, group=group, max_points=max_points)

    @router.get("/api/episodes/{episode_id}/video/{camera}")
    def get_video(episode_id: str, camera: str):
        try:
            paths = resolve_episode_paths(samples_root, episode_id)
        except DatahiveError as e:
            raise HTTPException(404, str(e))
        video_path = paths.videos.get(camera)
        if video_path is None or not video_path.exists():
            raise HTTPException(404, f"No video for camera '{camera}'")
        return FileResponse(video_path, media_type="video/mp4")

    @router.get("/api/episodes/{episode_id}/setup-image")
    def get_setup_image(episode_id: str):
        try:
            paths = resolve_episode_paths(samples_root, episode_id)
        except DatahiveError as e:
            raise HTTPException(404, str(e))
        if not paths.setup_jpg.exists():
            raise HTTPException(404, "No setup image")
        return FileResponse(paths.setup_jpg, media_type="image/jpeg")

    @router.post("/api/episodes/{episode_id}/validate")
    def post_validate(episode_id: str, payload: ValidatePayload):
        from datahive.annotate import annotate_episode
        from datahive.validate import validate_episode

        try:
            annotate_episode(samples_root, episode_id, payload.model_dump())
        except DatahiveError as e:
            raise HTTPException(422, str(e))

        try:
            warnings = validate_episode(samples_root, episode_id)
        except DatahiveError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "warnings": warnings}

    @router.post("/api/episodes/{episode_id}/upload")
    def post_upload(episode_id: str, force: bool = False):
        try:
            result = ops.upload_episode(samples_root, episode_id, force=force)
        except DatahiveError as e:
            raise HTTPException(400, str(e))
        return result.__dict__

    @router.delete("/api/episodes/{episode_id}")
    def delete_episode(episode_id: str, remote: bool = True):
        try:
            result = ops.delete_episode(samples_root, episode_id, delete_remote=remote)
        except DatahiveError as e:
            raise HTTPException(400, str(e))
        return result.__dict__

    @router.post("/api/sync")
    def post_sync(dry_run: bool = False):
        try:
            report = ops.sync(samples_root, dry_run=dry_run)
        except DatahiveError as e:
            raise HTTPException(400, str(e))
        return report.__dict__

    return router
