"""JSON API routes. Every route calls the same ops/annotate/validate
functions the CLI calls -- no reimplementation of business logic here."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from datahive import ops, runner
from datahive.collect import CollectError, CollectRuntime
from datahive.attachments import load_registry
from datahive.episode import episode_dataset_info, episode_stats, read_header, read_trajectory
from datahive.errors import DatahiveError
from datahive.index import Index
from datahive.paths import resolve_episode_paths, trials_csv_path
from datahive.profile import (
    derive_platform_id,
    SKELETON,
    incompleteness_problems,
    read_raw_profile,
    save_profile,
    write_profile_skeleton,
)
from datahive.trials import get_row, read_rows


class AnnotatePayload(BaseModel):
    attachment_id: Optional[str] = None
    operator_name: Optional[str] = None
    outcome: Optional[str] = None
    failure_cause: Optional[str] = None
    failure_cause_detail: Optional[str] = None
    severity: Optional[str] = None
    completion_time_s: Optional[float] = None
    completion_source: Optional[str] = None
    n_attempts: Optional[int] = None
    n_regrasps: Optional[int] = None
    stage_reached: Optional[int] = None
    strategy: Optional[str] = None
    annotator_name: Optional[str] = None
    notes: str = ""


class RunnerSessionPayload(BaseModel):
    operator_name: str
    lab_id: Optional[str] = None
    platform_id: Optional[str] = None
    date: Optional[str] = None
    attachment_ids: Optional[list[str]] = None
    per_task: int = 5
    randomize: bool = True
    seed: Optional[int] = None
    mode: str = "manual"


class RunnerPlanPayload(BaseModel):
    attachment_ids: Optional[list[str]] = None
    per_task: Optional[int] = None
    randomize: Optional[bool] = None
    seed: Optional[int] = None
    operator_name: Optional[str] = None


class RunnerModePayload(BaseModel):
    mode: str


class RunnerTrialPayload(BaseModel):
    trial_id: Optional[str] = None
    outcome: Optional[str] = None
    failure_cause: Optional[str] = None
    failure_cause_detail: Optional[str] = None
    severity: Optional[str] = None
    completion_time_s: Optional[float] = None
    n_attempts: Optional[int] = None
    n_regrasps: Optional[int] = None
    stage_reached: Optional[int] = None
    strategy: Optional[str] = None
    operator_name: Optional[str] = None
    annotator_name: Optional[str] = None
    notes: str = ""


class CollectSubmitPayload(BaseModel):
    session_id: str
    trial_id: Optional[str] = None
    instruction: Optional[str] = None


class CollectAnnotatingPayload(BaseModel):
    episode_id: str


class BulkIdsPayload(BaseModel):
    episode_ids: list[str]


class ProfilePayload(BaseModel):
    manipulator: dict = {}
    end_effector: dict = {}
    low_level: dict = {}
    control_mode: Optional[str] = None
    policy: Optional[str] = None
    cameras: list = []
    board_mounting: Optional[str] = None
    hiveboard_version: Optional[str] = None
    board_fabrication: dict = {}
    units_and_frames: dict = {}
    platform_id: Optional[str] = None
    robot_name: Optional[str] = None
    gripper_name: Optional[str] = None
    is_biarm: Any = None
    uses_mobile_base: Any = None
    control_freq: Any = None
    action_space: list = []
    action_joint_names: list = []
    orientation_representation: Optional[str] = None
    robot_state_orientation_representation: Optional[str] = None
    gains: Any = None
    intrinsic_calibration_matrix: Any = None
    extrinsic_calibration_matrix: Any = None


def build_router(samples_root: Path) -> APIRouter:
    router = APIRouter()
    collect_runtime = CollectRuntime()

    @router.get("/api/profile")
    def get_profile():
        raw = read_raw_profile(samples_root)
        if raw is None:
            return {"exists": False, "profile": SKELETON, "problems": incompleteness_problems(SKELETON)}
        return {"exists": True, "profile": raw, "problems": incompleteness_problems(raw)}

    @router.post("/api/profile")
    def create_profile(force: bool = False):
        """Creates samples/robot_profile.yaml (the same skeleton `datahive
        new-profile` writes), so a profile can be started from the web
        interface as well as the CLI."""
        try:
            write_profile_skeleton(samples_root, force=force)
        except FileExistsError as e:
            raise HTTPException(409, str(e))
        raw = read_raw_profile(samples_root)
        return {"exists": True, "profile": raw, "problems": incompleteness_problems(raw)}

    @router.put("/api/profile")
    def put_profile(payload: ProfilePayload):
        """Saves the full robot profile (create-or-update). Editing this file
        never touches already-recorded episodes, which keep the snapshot
        they were created with."""
        save_profile(samples_root, payload.model_dump())
        raw = read_raw_profile(samples_root)
        return {"exists": True, "profile": raw, "problems": incompleteness_problems(raw)}

    @router.get("/api/attachments")
    def get_attachments():
        registry = load_registry(samples_root)
        return {aid: vars(info) for aid, info in registry.items()}

    @router.get("/api/statistics")
    def get_statistics():
        registry = load_registry(samples_root)
        raw_profile = read_raw_profile(samples_root)
        profile_complete = raw_profile is not None and len(incompleteness_problems(raw_profile)) == 0

        with Index(samples_root) as idx:
            idx.scan()
            records = idx.all()

        session_ids = {r.session_id for r in records}
        if samples_root.exists():
            for p in samples_root.iterdir():
                if p.is_dir() and not p.name.startswith(".") and ((p / "trials.csv").is_file() or (p / runner.SESSION_FILE).is_file()):
                    session_ids.add(p.name)

        def attachment_of(record):
            try:
                paths = resolve_episode_paths(samples_root, record.episode_id, record.session_id)
                hdr = read_header(paths.h5)
                return hdr.task_ids[0] if hdr.task_ids else None
            except Exception:
                return None

        def trial_entry(tid, episode_id, row, attachment_id):
            info = registry.get(attachment_id) if attachment_id else None
            row = row or {}
            return {
                "trial_id": tid,
                "episode_id": episode_id,
                "attachment_id": attachment_id,
                "attachment_name": info.name if info else (attachment_id or "—"),
                "outcome": row.get("outcome") or "—",
                "completion_time_s": row.get("completion_time_s") or None,
                "n_attempts": row.get("n_attempts") or None,
                "n_regrasps": row.get("n_regrasps") or None,
                "stage_reached": row.get("stage_reached") or None,
            }

        plans: list[dict[str, Any]] = []
        for sid in sorted(session_ids):
            rows = {r["trial_id"]: dict(r) for r in read_rows(trials_csv_path(samples_root, sid)) if r.get("trial_id")}
            try:
                meta = runner.load_session(samples_root, sid)
            except DatahiveError:
                meta = None
            trials: list[dict[str, Any]] = []
            seen: set[str] = set()
            for r in (x for x in records if x.session_id == sid):
                tid = r.trial_id or r.episode_id
                if tid in seen:
                    continue
                seen.add(tid)
                row = rows.get(r.trial_id) if r.trial_id else None
                attachment_id = (row or {}).get("attachment_id") or attachment_of(r)
                trials.append({**trial_entry(tid, r.episode_id, row, attachment_id), "session_id": sid})
            for tid, row in (rows.items() if meta else ()):     # rows without an episode only count in a Runner plan
                if tid not in seen:
                    seen.add(tid)
                    trials.append({**trial_entry(tid, None, row, row.get("attachment_id") or None), "session_id": sid})
            if not trials and not meta:
                continue

            counts: dict[str, int] = {}
            for t in trials:
                if t["attachment_id"]:
                    counts[t["attachment_id"]] = counts.get(t["attachment_id"], 0) + 1
            if meta:
                targets: dict[str, int] = {}
                for e in meta["plan"]:
                    targets[e["attachment_id"]] = targets.get(e["attachment_id"], 0) + 1
                task_ids = [a for a in registry if a in targets]
            else:
                targets = {a: 5 for a in registry}
                task_ids = list(registry)
            per = meta.get("per_task", 5) if meta else 5
            tested = sum(1 for a in task_ids if counts.get(a, 0) > 0)
            all_recorded = bool(task_ids) and tested == len(task_ids)
            all_targets = bool(task_ids) and all(counts.get(a, 0) >= targets[a] for a in task_ids)
            valid_entries = bool(trials) and all(t["attachment_id"] in registry for t in trials)
            if meta:
                checks = [
                    {"label": "Experimental setup recorded", "passed": profile_complete},
                    {"label": f"All {len(task_ids)} conditions of this plan recorded", "passed": all_recorded},
                    {"label": f"All {sum(targets.values())} planned trials recorded", "passed": all_targets},
                    {"label": "Trial entries valid", "passed": valid_entries},
                ]
            else:
                checks = [
                    {"label": "Experimental setup recorded", "passed": profile_complete},
                    {"label": f"All {len(task_ids)} conditions recorded", "passed": all_recorded},
                    {"label": "5 trials recorded for every condition", "passed": all_targets},
                    {"label": "Trial entries valid", "passed": valid_entries},
                ]
            complete = all(c["passed"] for c in checks)
            plans.append({
                "session_id": sid,
                "date": meta["date"] if meta else None,
                "operator_name": meta["operator_name"] if meta else None,
                "mode": meta.get("mode", "manual") if meta else None,
                "has_plan": bool(meta),
                "summary": {
                    "total_trials": len(trials),
                    "successful_trials": sum(1 for t in trials if t["outcome"] == "success"),
                    "conditions_tested": tested,
                    "conditions_total": len(task_ids),
                    "target_trials_per_condition": per,
                    "total_target_trials": sum(targets.values()) if meta else len(task_ids) * 5,
                },
                "readiness": {
                    "is_complete": complete,
                    "status_label": "Trial records complete" if complete else "Trial records incomplete",
                    "status_class": "ready" if complete else "incomplete",
                    "checks": checks,
                },
                "conditions": [
                    {"id": a, "name": registry[a].name, "family": registry[a].family, "count": counts.get(a, 0),
                     "target": targets[a], "complete": counts.get(a, 0) >= targets[a]}
                    for a in task_ids
                ],
                "trials": trials,
            })
        plans.sort(key=lambda p: (p["date"] or "", p["session_id"]), reverse=True)

        # "All plans": the same numbers over every plan together (each against the full 13 x 5 goal).
        all_trials = [t for p in plans for t in p["trials"]]
        condition_counts = {aid: 0 for aid in registry}
        for t in all_trials:
            if t["attachment_id"]:
                condition_counts[t["attachment_id"]] = condition_counts.get(t["attachment_id"], 0) + 1
        conditions_total = len(registry)
        conditions_tested = sum(1 for c in condition_counts.values() if c > 0)
        all_conditions_recorded = all(condition_counts.get(k, 0) > 0 for k in registry) if conditions_total else False
        five_per = all(condition_counts.get(k, 0) >= 5 for k in registry) if conditions_total else False
        trials_valid = bool(all_trials) and all(t["attachment_id"] in registry for t in all_trials)
        checks = [
            {"label": "Experimental setup recorded", "passed": profile_complete},
            {"label": f"All {conditions_total} conditions recorded", "passed": all_conditions_recorded},
            {"label": "5 trials recorded for every condition", "passed": five_per},
            {"label": "Trial entries valid", "passed": trials_valid},
        ]
        is_complete = all(c["passed"] for c in checks)
        return {
            "summary": {
                "total_trials": len(all_trials),
                "successful_trials": sum(1 for t in all_trials if t["outcome"] == "success"),
                "conditions_tested": conditions_tested,
                "conditions_total": conditions_total,
                "target_trials_per_condition": 5,
                "total_target_trials": conditions_total * 5,
            },
            "readiness": {
                "is_complete": is_complete,
                "status_label": "Trial records complete" if is_complete else "Trial records incomplete",
                "status_class": "ready" if is_complete else "incomplete",
                "checks": checks,
            },
            "conditions": [
                {"id": aid, "name": info.name, "family": info.family, "count": condition_counts.get(aid, 0),
                 "target": 5, "complete": condition_counts.get(aid, 0) >= 5}
                for aid, info in registry.items()
            ],
            "trials": all_trials,
            "plans": plans,
        }

    @router.get("/api/episodes")
    def list_episodes(q: Optional[str] = None, status: Optional[str] = None):
        try:
            episodes = ops.list_episodes(samples_root, status=status, query=q)
        except DatahiveError as e:
            raise HTTPException(400, str(e))
        return [e.__dict__ for e in episodes]

    @router.post("/api/episodes/upload-preflight")
    def upload_preflight(payload: BulkIdsPayload):
        from datahive.preflight import upload_preflight as run_preflight

        try:
            hub = ops._get_hub(None)
        except DatahiveError:
            hub = None
        return run_preflight(samples_root, payload.episode_ids, hub=hub)

    @router.post("/api/episodes/bulk-upload")
    def bulk_upload(payload: BulkIdsPayload, force: bool = False):
        """Uploads several episodes in one call, reusing the same Hub
        client (and the same ops.upload_episode a single upload uses)."""
        results = ops.bulk_upload_episodes(samples_root, payload.episode_ids, force=force)
        return {"results": [r.__dict__ for r in results]}

    @router.post("/api/episodes/bulk-delete")
    def bulk_delete(payload: BulkIdsPayload, remote: bool = True):
        results = ops.bulk_delete_episodes(samples_root, payload.episode_ids, delete_remote=remote)
        return {"results": [r.__dict__ for r in results]}

    @router.post("/api/episodes/bulk-validate")
    def bulk_validate(payload: BulkIdsPayload):
        from datahive.validate import bulk_validate_episodes

        return {"results": bulk_validate_episodes(samples_root, payload.episode_ids)}

    @router.get("/api/episodes/{episode_id}/previous-annotations")
    def list_previous_annotations(episode_id: str, limit: int = 20):
        return {"results": ops.list_recent_annotations(samples_root, exclude_episode_id=episode_id, limit=limit)}

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
        from datahive.consistency import missing_parts, video_duration_s

        defaults: dict = {}
        if header.task_ids:
            defaults["attachment_id"] = header.task_ids[0]
        try:
            defaults["operator_name"] = runner.load_session(samples_root, header.session_id).get("operator_name", "")
        except DatahiveError:
            pass
        return {
            "header": header.model_dump(mode="json"),
            "annotation": row,
            "annotation_defaults": defaults,
            "missing": missing_parts(paths, header),
            "completion_sources": {
                "timer": header.manual_timer_s,
                "hdf5": episode_stats(paths.h5).get("duration_s"),
                "video": video_duration_s(paths, header),
            },
            "index": vars(rec) if rec else None,
            "cameras": sorted(paths.videos.keys()),
            "has_setup_image": paths.setup_jpg.exists(),
            "stats": episode_stats(paths.h5),
            "dataset_info": episode_dataset_info(paths.h5),
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

    @router.post("/api/episodes/{episode_id}/annotate")
    def post_annotate(episode_id: str, payload: AnnotatePayload):
        """Saves the trials.csv row only -- distinct from /validate, which
        the CLI's `datahive annotate` (auto-validates after) and `datahive
        validate` also keep separate. This lets the GUI's Save button save
        a draft without also having to pass validation."""
        from datahive.annotate import annotate_episode

        try:
            annotate_episode(samples_root, episode_id, payload.model_dump(), validate_after=False)
        except DatahiveError as e:
            raise HTTPException(422, str(e))
        collect_runtime.mark_annotated(episode_id)
        return {"ok": True}

    @router.post("/api/episodes/{episode_id}/validate")
    def post_validate(episode_id: str):
        """Runs schema/profile validation only, against whatever is
        currently saved -- the same check `datahive validate` runs."""
        from datahive.validate import validate_episode

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

    @router.get("/api/status")
    def get_status():
        """Cheap Hub connectivity + pending-upload summary for the header's
        sync indicator. Never raises -- an unreachable Hub or missing
        config is a normal state to display, not a 4xx/5xx."""
        return ops.get_sync_status(samples_root).__dict__

    # ---- Trial/evaluation runner -------------------------------------------------

    @router.get("/api/runner/defaults")
    def runner_defaults():
        from datetime import datetime, timezone

        from datahive.config import load_config

        try:
            lab_id = load_config().lab_id
        except DatahiveError:
            lab_id = ""
        raw = read_raw_profile(samples_root) or {}
        return {
            "lab_id": lab_id,
            "platform_id": raw.get("platform_id") or derive_platform_id(raw.get("robot_name"), raw.get("gripper_name")),
            "date": datetime.now(timezone.utc).date().isoformat(),
            "required_trials": runner.REQUIRED_TRIALS,
            "samples_root": str(samples_root.resolve()),
            "profile_problems": incompleteness_problems(raw) if raw else ["No robot profile yet."],
        }

    @router.get("/api/runner/sessions")
    def runner_sessions():
        return runner.list_sessions(samples_root)

    @router.post("/api/runner/sessions")
    def runner_create_session(payload: RunnerSessionPayload):
        defaults = runner_defaults()
        try:
            session = runner.create_session(
                samples_root,
                lab_id=payload.lab_id or defaults["lab_id"],
                platform_id=payload.platform_id or defaults["platform_id"],
                operator_name=payload.operator_name,
                date=payload.date,
                attachment_ids=payload.attachment_ids,
                per_task=payload.per_task,
                randomize=payload.randomize,
                seed=payload.seed,
                mode=payload.mode,
            )
        except DatahiveError as e:
            raise HTTPException(422, str(e))
        return runner.session_progress(samples_root, session["session_id"])

    @router.get("/api/runner/sessions/{session_id}")
    def runner_session(session_id: str):
        try:
            return runner.session_progress(samples_root, session_id)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))

    @router.delete("/api/runner/sessions/{session_id}")
    def runner_delete_session(session_id: str):
        try:
            runner.delete_session(samples_root, session_id)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        except DatahiveError as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @router.put("/api/runner/sessions/{session_id}/plan")
    def runner_edit_plan(session_id: str, payload: RunnerPlanPayload):
        try:
            runner.edit_plan(samples_root, session_id, **payload.model_dump())
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        except DatahiveError as e:
            raise HTTPException(422, str(e))
        return runner.session_progress(samples_root, session_id)

    @router.patch("/api/runner/sessions/{session_id}")
    def runner_set_mode(session_id: str, payload: RunnerModePayload):
        try:
            runner.set_session_mode(samples_root, session_id, payload.mode)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        except DatahiveError as e:
            raise HTTPException(422, str(e))
        return runner.session_progress(samples_root, session_id)

    @router.post("/api/runner/sessions/{session_id}/trials/{trial_id}/episode")
    async def runner_upload_episode(session_id: str, trial_id: str, request: Request, partial: bool = False, empty: bool = False,
                                     timer_s: Optional[float] = None):
        """Manual mode: the operator uploads the HDF5 (field `h5`) and one video per
        robot-profile camera (fields `video:<camera>`). Valid only if all are present
        and the episode passes validation."""
        import shutil
        import tempfile

        form = await request.form()
        tmp_root = samples_root / ".datahive" / "uploads"
        tmp_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(dir=tmp_root))
        try:
            h5 = form.get("h5")
            h5_path = None
            if h5 is not None and hasattr(h5, "file"):
                h5_path = tmp / "episode.h5"
                with open(h5_path, "wb") as out:
                    shutil.copyfileobj(h5.file, out)
            elif not partial:
                raise HTTPException(422, "The HDF5 file (field 'h5') is required.")
            videos: dict[str, Path] = {}
            for key, value in form.multi_items():
                if key.startswith("video:") and hasattr(value, "file"):
                    cam = key[len("video:"):]
                    if not cam.replace("_", "").replace("-", "").isalnum():
                        raise HTTPException(422, f"Invalid camera name {cam!r}.")
                    path = tmp / f"{cam}.mp4"
                    with open(path, "wb") as out:
                        shutil.copyfileobj(value.file, out)
                    videos[cam] = path
            try:
                return runner.add_episode_files(samples_root, session_id, trial_id, h5_path, videos, partial=partial, allow_empty=empty, timer_s=timer_s)
            except runner.SessionNotFound as e:
                raise HTTPException(404, str(e))
            except DatahiveError as e:
                raise HTTPException(422, str(e))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @router.post("/api/runner/sessions/{session_id}/trials")
    def runner_record_trial(session_id: str, payload: RunnerTrialPayload):
        data = payload.model_dump()
        try:
            row = runner.record_trial(samples_root, session_id, data.pop("trial_id"), data)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        except DatahiveError as e:
            raise HTTPException(422, str(e))
        return {"ok": True, "row": row, "session": runner.session_progress(samples_root, session_id)}

    @router.delete("/api/runner/sessions/{session_id}/trials/{trial_id}")
    def runner_remove_trial(session_id: str, trial_id: str):
        try:
            removed = runner.remove_trial(samples_root, session_id, trial_id)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        return {"ok": removed, "session": runner.session_progress(samples_root, session_id)}

    @router.get("/api/runner/sessions/{session_id}/trials.csv")
    def runner_trials_csv(session_id: str):
        """The trials of this session that have a valid episode (nothing else is exported)."""
        import csv
        import io

        from fastapi.responses import Response

        from datahive.schema import TRIAL_COLUMNS

        try:
            progress = runner.session_progress(samples_root, session_id)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        rows = [p["row"] for p in progress["plan"] if p["row"] and p["episode"] and p["episode"]["valid"]]
        if not rows:
            raise HTTPException(409, "No trial with a valid episode yet: annotate and validate an episode first.")
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(TRIAL_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in TRIAL_COLUMNS})
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="trials.csv"'})

    # ---- Human-in-the-loop collection ---------------------------------------------

    @router.get("/api/collect/state")
    def collect_state():
        return collect_runtime.snapshot()

    @router.post("/api/collect/submit")
    def collect_submit(payload: CollectSubmitPayload):
        try:
            progress = runner.session_progress(samples_root, payload.session_id)
        except runner.SessionNotFound as e:
            raise HTTPException(404, str(e))
        entry = None
        if payload.trial_id is None:
            entry = progress["next_trial"]
        else:
            entry = next((p for p in progress["plan"] if p["trial_id"] == str(payload.trial_id)), None)
        if entry is None:
            raise HTTPException(422, "No pending trial in this session; every planned trial is recorded.")
        info = load_registry(samples_root)[entry["attachment_id"]]
        task = {
            "session_id": payload.session_id,
            "trial_id": entry["trial_id"],
            "attachment_id": entry["attachment_id"],
            "name": info.name,
            "family": info.family,
            "timeout": info.timeout,
            "stages": info.stages,
            "image": info.image,
            "success": info.success,
            "reset": info.reset,
            "instruction": payload.instruction or info.success,
            "lab_id": progress["lab_id"],
            "platform_id": progress["platform_id"],
            "operator_name": progress["operator_name"],
        }
        try:
            collect_runtime.submit(task)
        except CollectError as e:
            raise HTTPException(409, str(e))
        return collect_runtime.snapshot()

    @router.post("/api/collect/start")
    def collect_start():
        try:
            collect_runtime.start()
        except CollectError as e:
            raise HTTPException(409, str(e))
        return collect_runtime.snapshot()

    @router.post("/api/collect/annotating")
    def collect_annotating(payload: CollectAnnotatingPayload):
        try:
            collect_runtime.annotating(payload.episode_id)
        except CollectError as e:
            raise HTTPException(409, str(e))
        return collect_runtime.snapshot()

    @router.post("/api/collect/done")
    def collect_done():
        collect_runtime.done()
        return collect_runtime.snapshot()

    @router.post("/api/collect/abort")
    def collect_abort():
        collect_runtime.abort()
        return collect_runtime.snapshot()

    return router
