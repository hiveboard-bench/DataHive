"""Trial/evaluation runner backend: sessions, generated trial plans, and
trial records, following HiveBoard's Evaluation Runner.

A session is a folder samples/<session_id>/ holding session.json (who, when,
and the generated plan) and trials.csv (one row per recorded trial). The same
folder later receives the episodes recorded for those trials, so a trial row,
its episode and its annotation all share one trial_id.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from datahive.attachments import load_registry
from datahive.errors import AnnotationError, DatahiveError, ValidationError
from datahive.index import Index
from datahive.paths import session_dir, trials_csv_path
from datahive.schema import ANNOTATION_SCHEMA_CURRENT, TrialAnnotation
from datahive.trials import delete_row, read_rows, upsert_row

REQUIRED_TRIALS = 5
MODES = ("manual", "automatic")
SESSION_FILE = "session.json"
_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class SessionNotFound(DatahiveError):
    pass


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def session_path(samples_root: Path, session_id: str) -> Path:
    return session_dir(samples_root, session_id) / SESSION_FILE


def generate_plan(
    attachment_ids: list[str], per_task: int = REQUIRED_TRIALS, *, randomize: bool = True, seed: int | None = None
) -> list[dict[str, str]]:
    """Trial queue: `per_task` trials for each attachment. Ordered by task, or
    shuffled (reproducibly with `seed`) so conditions are interleaved. Trial ids
    are the positions in the run order, 1..N."""
    entries = [aid for aid in attachment_ids for _ in range(per_task)]
    if randomize:
        random.Random(seed).shuffle(entries)
    return [{"trial_id": str(i + 1), "attachment_id": aid} for i, aid in enumerate(entries)]


def create_session(
    samples_root: Path,
    *,
    lab_id: str,
    platform_id: str,
    operator_name: str,
    date: str | None = None,
    attachment_ids: list[str] | None = None,
    per_task: int = REQUIRED_TRIALS,
    randomize: bool = True,
    seed: int | None = None,
    session_id: str | None = None,
    mode: str = "manual",
) -> dict[str, Any]:
    if mode not in MODES:
        raise DatahiveError(f"mode must be one of {list(MODES)}.")
    lab_id, platform_id, operator_name = lab_id.strip(), platform_id.strip(), operator_name.strip()
    if not lab_id or not _ID_RE.match(lab_id):
        raise DatahiveError("lab_id is required (letters, digits, '_' and '-').")
    if not platform_id or not _ID_RE.match(platform_id):
        raise DatahiveError("platform_id is required (letters, digits, '_' and '-'); set it in the robot profile.")
    if not operator_name:
        raise DatahiveError("operator_name is required.")
    date = date or _utc_today()
    try:
        datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        raise DatahiveError(f"date must be YYYY-MM-DD, got {date!r}.")
    if per_task < 1 or per_task > 50:
        raise DatahiveError("per_task must be between 1 and 50.")

    registry = load_registry(samples_root)
    ids = attachment_ids or list(registry)
    unknown = [a for a in ids if a not in registry]
    if unknown:
        raise DatahiveError(f"Unknown attachment(s): {unknown}.")

    base = session_id or f"{lab_id}_{platform_id}_{date.replace('-', '')}"
    if not _ID_RE.match(base):
        raise DatahiveError("session_id may only contain letters, digits, '_' and '-'.")
    sid, n = base, 1
    while session_path(samples_root, sid).exists():
        n += 1
        sid = f"{base}_{n}"

    data = {
        "session_id": sid,
        "lab_id": lab_id,
        "platform_id": platform_id,
        "operator_name": operator_name,
        "date": date,
        "per_task": per_task,
        "randomized": randomize,
        "seed": seed,
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "plan": generate_plan(ids, per_task, randomize=randomize, seed=seed),
    }
    path = session_path(samples_root, sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def load_session(samples_root: Path, session_id: str) -> dict[str, Any]:
    if not _ID_RE.match(session_id or ""):
        raise SessionNotFound(f"Invalid session id {session_id!r}.")
    path = session_path(samples_root, session_id)
    if not path.is_file():
        raise SessionNotFound(f"No runner session '{session_id}'.")
    return json.loads(path.read_text(encoding="utf-8"))


def edit_plan(
    samples_root: Path,
    session_id: str,
    *,
    attachment_ids: list[str] | None = None,
    per_task: int | None = None,
    randomize: bool | None = None,
    seed: int | None = None,
    operator_name: str | None = None,
) -> dict[str, Any]:
    """Re-plans the trials that are not recorded yet.

    Recorded trials keep their ids, tasks and results. Tasks can be added, or
    removed if they have no recorded trial; `per_task` is the target number of
    trials for each task (never below what is already recorded); the remaining
    trials are renumbered after the last recorded id, ordered or shuffled."""
    from collections import Counter

    session = load_session(samples_root, session_id)
    registry = load_registry(samples_root)
    recorded_ids = {r["trial_id"] for r in read_rows(trials_csv_path(samples_root, session_id)) if r.get("trial_id")}
    recorded_ids |= set(trial_episodes(samples_root, session_id))
    recorded = sorted((e for e in session["plan"] if e["trial_id"] in recorded_ids), key=lambda e: int(e["trial_id"]))
    done = Counter(e["attachment_id"] for e in recorded)

    current = {e["attachment_id"] for e in session["plan"]}
    ids = list(attachment_ids) if attachment_ids is not None else [a for a in registry if a in current]
    unknown = [a for a in ids if a not in registry]
    if unknown:
        raise DatahiveError(f"Unknown attachment(s): {unknown}.")
    if not ids:
        raise DatahiveError("Select at least one task.")
    locked = sorted(a for a in done if a not in ids)
    if locked:
        raise DatahiveError(f"Tasks with recorded trials cannot be removed: {locked}.")
    per = session["per_task"] if per_task is None else per_task
    if per < 1 or per > 50:
        raise DatahiveError("per_task must be between 1 and 50.")
    if operator_name is not None and not operator_name.strip():
        raise DatahiveError("operator_name is required.")
    randomize = session.get("randomized", True) if randomize is None else randomize

    ordered = [a for a in registry if a in ids]
    entries = [a for a in ordered for _ in range(max(0, per - done.get(a, 0)))]
    if randomize:
        random.Random(seed).shuffle(entries)
    start = max((int(e["trial_id"]) for e in recorded), default=0) + 1
    session["plan"] = recorded + [{"trial_id": str(start + i), "attachment_id": a} for i, a in enumerate(entries)]
    session.update(per_task=per, randomized=randomize, seed=seed)
    if operator_name is not None:
        session["operator_name"] = operator_name.strip()
    session_path(samples_root, session_id).write_text(json.dumps(session, indent=2), encoding="utf-8")
    return session


def delete_session(samples_root: Path, session_id: str) -> None:
    """Deletes the session plan and its trials. Refuses if episodes were recorded
    for it, so recorded data is never lost by accident."""
    from datahive.paths import episodes_dir

    load_session(samples_root, session_id)
    edir = episodes_dir(samples_root, session_id)
    n = len(list(edir.glob("*.h5"))) if edir.is_dir() else 0
    if n:
        raise DatahiveError(
            f"Session '{session_id}' has {n} episode(s). Delete them in Annotate first, then delete the session."
        )
    for path in (session_path(samples_root, session_id), trials_csv_path(samples_root, session_id),
                 trials_csv_path(samples_root, session_id).with_suffix(".csv.lock")):
        path.unlink(missing_ok=True)
    folder = session_dir(samples_root, session_id)
    try:
        edir.rmdir()
    except OSError:
        pass
    try:
        folder.rmdir() if not any(folder.iterdir()) else None
    except OSError:
        pass


def set_session_mode(samples_root: Path, session_id: str, mode: str) -> dict[str, Any]:
    if mode not in MODES:
        raise DatahiveError(f"mode must be one of {list(MODES)}.")
    session = load_session(samples_root, session_id)
    session["mode"] = mode
    session_path(samples_root, session_id).write_text(json.dumps(session, indent=2), encoding="utf-8")
    return session


def list_sessions(samples_root: Path) -> list[dict[str, Any]]:
    out = []
    if not samples_root.is_dir():
        return out
    for child in sorted(samples_root.iterdir(), reverse=True):
        if child.is_dir() and not child.name.startswith(".") and (child / SESSION_FILE).is_file():
            try:
                out.append(session_progress(samples_root, child.name))
            except Exception:
                continue
    return out


def trial_episodes(samples_root: Path, session_id: str) -> dict[str, dict[str, Any]]:
    """Episodes of a session by trial id, with whether each is complete and valid."""
    from datahive.consistency import missing_parts
    from datahive.episode import read_header
    from datahive.paths import resolve_episode_paths

    episodes: dict[str, dict[str, Any]] = {}
    with Index(samples_root) as idx:
        idx.scan()
        for rec in idx.all():
            if rec.session_id == session_id and rec.trial_id:
                try:
                    paths = resolve_episode_paths(samples_root, rec.episode_id, rec.session_id)
                    missing = missing_parts(paths, read_header(paths.h5))
                except Exception:
                    missing = []
                episodes[rec.trial_id] = {"episode_id": rec.episode_id, "status": rec.effective_status,
                                          "valid": rec.status in ("validated", "uploaded") and not missing,
                                          "incomplete": bool(missing), "missing": missing}
    return episodes


def session_progress(samples_root: Path, session_id: str) -> dict[str, Any]:
    """The session plus what is recorded so far and what to run next."""
    session = load_session(samples_root, session_id)
    registry = load_registry(samples_root)
    rows = {r["trial_id"]: r for r in read_rows(trials_csv_path(samples_root, session_id)) if r.get("trial_id")}
    episodes = trial_episodes(samples_root, session_id)
    plan, per_task = [], {}
    next_trial = None
    for entry in session["plan"]:
        row = rows.get(entry["trial_id"])
        info = registry.get(entry["attachment_id"])
        episode = episodes.get(entry["trial_id"])
        item = {**entry, "recorded": row is not None or episode is not None, "annotated": row is not None,
                "row": row, "episode": episode}
        plan.append(item)
        counts = per_task.setdefault(entry["attachment_id"], {"planned": 0, "recorded": 0})
        counts["planned"] += 1
        counts["recorded"] += 1 if item["recorded"] else 0
        if not item["recorded"] and next_trial is None:
            next_trial = {**entry, "name": info.name if info else entry["attachment_id"]}
    per_task = {aid: per_task[aid] for aid in registry if aid in per_task}
    recorded = sum(1 for p in plan if p["recorded"])
    return {
        "mode": "manual",
        **{k: v for k, v in session.items() if k != "plan"},
        "plan": plan,
        "per_task": per_task,
        "trials_per_task": session.get("per_task", REQUIRED_TRIALS),
        "n_planned": len(plan),
        "n_recorded": recorded,
        "n_success": sum(1 for p in plan if p["row"] and p["row"].get("outcome") == "success"),
        "n_incomplete": sum(1 for p in plan if p["episode"] and p["episode"]["incomplete"]),
        "next_trial": next_trial,
        "n_episodes_valid": sum(1 for p in plan if p["episode"] and p["episode"]["valid"]),
        "complete": recorded == len(plan),
    }


def _blank(v: Any) -> Any:
    return None if v in ("", None) else v


def record_trial(samples_root: Path, session_id: str, trial_id: str | None, fields: dict[str, Any]) -> dict[str, str]:
    """Validates and stores one trial of the session's plan (trials.csv row)."""
    session = load_session(samples_root, session_id)
    plan = {e["trial_id"]: e for e in session["plan"]}
    if trial_id is None:
        pending = session_progress(samples_root, session_id)["next_trial"]
        if pending is None:
            raise AnnotationError("Every planned trial is already recorded.")
        trial_id = pending["trial_id"]
    trial_id = str(trial_id)
    if trial_id not in plan:
        raise AnnotationError(f"Trial {trial_id} is not part of session '{session_id}'.")
    attachment_id = plan[trial_id]["attachment_id"]
    info = load_registry(samples_root)[attachment_id]

    outcome = fields.get("outcome")
    data = {
        "trial_id": trial_id,
        "lab_id": session["lab_id"],
        "platform_id": session["platform_id"],
        "attachment_id": attachment_id,
        "date": session["date"],
        "operator_name": fields.get("operator_name") or session["operator_name"],
        "annotator_name": fields.get("annotator_name") or session["operator_name"],
        "outcome": outcome,
        "failure_cause": _blank(fields.get("failure_cause")),
        "failure_cause_detail": fields.get("failure_cause_detail") or "",
        "severity": _blank(fields.get("severity")),
        "completion_time_s": _blank(fields.get("completion_time_s")),
        "n_attempts": _blank(fields.get("n_attempts")) if fields.get("n_attempts") is not None else 1,
        "n_regrasps": _blank(fields.get("n_regrasps")) if fields.get("n_regrasps") is not None else 0,
        "stage_reached": _blank(fields.get("stage_reached")),
        "strategy": fields.get("strategy"),
        "notes": fields.get("notes") or "",
        "annotated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": ANNOTATION_SCHEMA_CURRENT,
    }
    try:
        annotation = TrialAnnotation.model_validate(data, context={"composed_assembly": info.composed_assembly})
    except Exception as e:
        raise AnnotationError(f"Invalid trial: {e}") from e

    if outcome == "success" and info.timeout and annotation.completion_time_s > info.timeout:
        raise AnnotationError(
            f"Completion time {annotation.completion_time_s}s exceeds the {info.timeout}s timeout; record it as 'timeout'."
        )
    if info.composed_assembly and info.stages:
        n = len(info.stages)
        if annotation.stage_reached is not None and annotation.stage_reached > n:
            raise AnnotationError(f"stage_reached cannot exceed the {n} stages of '{attachment_id}'.")
        if outcome == "success" and annotation.stage_reached != n:
            raise AnnotationError("Success requires completing every stage.")

    upsert_row(trials_csv_path(samples_root, session_id), annotation)
    return annotation.to_csv_row()


def remove_trial(samples_root: Path, session_id: str, trial_id: str) -> bool:
    load_session(samples_root, session_id)
    return delete_row(trials_csv_path(samples_root, session_id), str(trial_id))


_CAMERA_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def add_episode_files(
    samples_root: Path, session_id: str, trial_id: str, h5_src: Path | None,
    videos: dict[str, Path], *, partial: bool = False, allow_empty: bool = False,
    timer_s: float | None = None,
) -> dict[str, Any]:
    """Adds an operator-supplied HDF5 and/or videos to the episode of a trial.

    Strict (default, used by the Runner): the HDF5 and one video for every
    robot-profile camera are all required. With `partial=True` (Annotate) any
    subset is accepted and merged into what the episode already has; whatever is
    still missing marks the episode as incomplete. The header is rebuilt from the
    robot profile and the session, so an uploaded HDF5 only has to carry the
    recorded data (/proprioception, /commands). The trial does not need to be
    annotated first: that happens afterwards, in Annotate."""
    import shutil

    import h5py

    from datahive.annotate import write_h5_annotation
    from datahive.consistency import missing_parts, probe_video
    from datahive.episode import make_header, read_header, write_header
    from datahive.paths import episodes_dir, resolve_episode_paths
    from datahive.profile import load_profile
    from datahive.validate import validate_episode

    session = load_session(samples_root, session_id)
    trial_id = str(trial_id)
    entry = next((e for e in session["plan"] if e["trial_id"] == trial_id), None)
    if entry is None:
        raise DatahiveError(f"Trial {trial_id} is not part of session '{session_id}'.")
    row = next((r for r in read_rows(trials_csv_path(samples_root, session_id)) if r.get("trial_id") == trial_id), None)

    profile = load_profile(samples_root)
    cameras = [c["name"] for c in profile.cameras]
    if not all(_CAMERA_RE.match(c) for c in cameras):
        raise DatahiveError("Camera names may only contain letters, digits, '_' and '-'.")
    extra = sorted(set(videos) - set(cameras))
    if extra:
        raise DatahiveError(f"Videos were given for cameras that are not in the robot profile: {extra}.")
    if partial:
        if h5_src is None and not videos and not allow_empty:
            raise DatahiveError("Choose at least one file to upload.")
    else:
        missing_v = [c for c in cameras if c not in videos]
        if missing_v:
            raise DatahiveError(f"A video is required for every camera in the robot profile. Missing: {missing_v}.")

    old_attrs: dict = {}
    if h5_src is not None:
        try:
            with h5py.File(h5_src, "r") as f:
                if "proprioception" not in f or "timestamp" not in f["proprioception"]:
                    raise DatahiveError("The HDF5 file has no /proprioception/timestamp dataset.")
                old_attrs = dict(f.attrs)
        except OSError as e:
            raise DatahiveError(f"The uploaded file is not a readable HDF5 file: {e}") from e

    episode_id = f"{session_id}_t{trial_id}"
    edir = episodes_dir(samples_root, session_id)
    edir.mkdir(parents=True, exist_ok=True)
    h5_dest = edir / f"{episode_id}.h5"
    existed = h5_dest.exists()
    rebuild = h5_src is not None or not existed
    previous_timer = None
    if existed:
        try:
            previous_timer = read_header(h5_dest).manual_timer_s
        except Exception:
            previous_timer = None
    if h5_src is not None:
        h5_dest.unlink(missing_ok=True)
        shutil.move(str(h5_src), str(h5_dest))
    elif not existed:
        with h5py.File(h5_dest, "w"):
            pass 
    for name, src in videos.items():
        dest = edir / f"{episode_id}_cam_{name}.mp4"
        dest.unlink(missing_ok=True)
        shutil.move(str(src), str(dest))

    if rebuild:
        header = make_header(
            profile, session_id=session_id, episode_id=episode_id, trial_id=trial_id,
            task_ids=[entry["attachment_id"]], lab_id=session["lab_id"], collection_mode="manual",
            policy=str(old_attrs["policy"]) if isinstance(old_attrs.get("policy"), str) and old_attrs.get("policy") else None,
        )
    else:
        header = read_header(h5_dest)
    if timer_s is not None:
        header.manual_timer_s = round(float(timer_s), 3)
    elif header.manual_timer_s is None and previous_timer is not None:
        header.manual_timer_s = previous_timer
    for cam in header.cameras:
        dest = edir / f"{episode_id}_cam_{cam['name']}.mp4"
        if dest.is_file():
            cam["file"] = dest.name
            try:
                info = probe_video(dest)
            except ValueError:
                info = None
            if info:
                cam["resolution"] = f"{info['width']}x{info['height']}"
                cam["fps"] = round(info["fps"], 3)
                cam["encoding"] = info["encoding"]
        else:
            cam.pop("file", None)
    with h5py.File(h5_dest, "r+") as f:
        for key in list(f.attrs):
            del f.attrs[key]
        write_header(f, header)
    if row is not None and rebuild:
        write_h5_annotation(h5_dest, TrialAnnotation.from_csv_row(row))

    paths = resolve_episode_paths(samples_root, episode_id)
    missing = missing_parts(paths, read_header(h5_dest))
    problems: list[str] = []
    warnings: list[str] = []
    if not missing:
        try:
            warnings = validate_episode(samples_root, episode_id, update_index=row is not None)
        except ValidationError as e:
            problems = [p for p in e.problems if "No annotation row found" not in p]
    return {"episode_id": episode_id, "valid": not missing and not problems, "incomplete": bool(missing),
            "missing": missing, "needs_annotation": row is None, "problems": problems,
            "warnings": warnings, "cameras": cameras}
