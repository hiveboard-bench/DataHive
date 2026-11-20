"""Write one source episode as a ``datahive_episode_v1`` episode."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from datahive.annotate import annotate_episode
from datahive.check import MAX_DURATION_S, MIN_DURATION_S
from datahive.episode import EpisodeWriter
from datahive.trials import read_rows

from . import video as video_mod
from .annotations import AnnotationPolicy, fields_for
from .common import ConversionError, EpisodeRecord, Skip
from .resample import provenance, sample_times, to_grid
from .sources.base import DatasetSource


@dataclass
class ConvertOptions:
    samples_root: Path
    session_id: str
    lab_id: str
    profile: Any
    annotation_policy: AnnotationPolicy
    target_hz: float = 100.0
    source_joint_units: str = "radians"
    video_mode: str = "transcode"
    crf: int = 19
    preset: str = "medium"
    collection_mode: str = "manual"
    overwrite: bool = False
    validate: bool = True
    keep_invalid: bool = False


def episode_paths(options: ConvertOptions, episode_id: str) -> tuple[Path, Path]:
    session_dir = Path(options.samples_root) / options.session_id
    return session_dir / "episodes" / f"{episode_id}.h5", session_dir


def episode_is_complete(options: ConvertOptions, episode_id: str, trial_id: int) -> bool:
    h5_path, session_dir = episode_paths(options, episode_id)
    if not h5_path.exists():
        return False
    cameras = [cam["name"] for cam in options.profile.cameras]
    if not all((session_dir / "episodes" / f"{episode_id}_cam_{cam}.mp4").exists() for cam in cameras):
        return False
    csv_path = session_dir / f"trials_{options.session_id}.csv"
    return any(row.get("trial_id") == str(trial_id) for row in read_rows(csv_path))


def convert_episode(
    source: DatasetSource,
    source_index: int,
    trial_id: int,
    episode_id: str,
    options: ConvertOptions,
) -> EpisodeRecord:
    meta = source.episode_meta(source_index)
    episode = source.read_episode(source_index)
    if episode.state.shape[0] != meta.length:
        raise ConversionError(
            f"episode {source_index}: {episode.state.shape[0]} state rows but metadata length {meta.length}"
        )

    arms_state, grip_state, arms_action, grip_action, joint_names = _split_gripper(
        episode, options.profile
    )
    if options.source_joint_units == "degrees":
        # The profile and header always describe arm joints in radians.
        arms_state = np.deg2rad(arms_state)
        arms_action = np.deg2rad(arms_action)
    elif options.source_joint_units != "radians":
        raise ConversionError(
            f"unsupported source joint units {options.source_joint_units!r}; "
            "use 'radians' or 'degrees'"
        )

    rate = source.info.fps
    target_hz = float(options.target_hz)
    arms_state, grip_state, arms_action, grip_action = (
        to_grid(arms_state, rate, target_hz),
        to_grid(grip_state, rate, target_hz),
        to_grid(arms_action, rate, target_hz),
        to_grid(grip_action, rate, target_hz),
    )
    n = arms_state.shape[0]
    duration = (n - 1) / target_hz
    if not MIN_DURATION_S <= duration <= MAX_DURATION_S:
        raise Skip(f"episode duration {duration:.2f}s is outside {MIN_DURATION_S:g}-{MAX_DURATION_S:g}s")
    for name, array in (("state", arms_state), ("action", arms_action)):
        if not np.isfinite(array).all():
            raise ConversionError(f"non-finite values in resampled {name}")

    annotation = options.annotation_policy.resolve(source_index)
    fields = fields_for(annotation, policy=options.annotation_policy)
    prov = provenance(rate, target_hz)

    h5_path, session_dir = episode_paths(options, episode_id)
    episodes_dir = session_dir / "episodes"
    scratch = Path(options.samples_root) / ".conversion-tmp" / episode_id
    created_videos = [episodes_dir / f"{episode_id}_cam_{cam['name']}.mp4" for cam in options.profile.cameras]
    touched = False
    try:
        if h5_path.exists() and not options.overwrite:
            raise ConversionError(f"{h5_path.name} already exists; pass --overwrite to replace it")
        _remove_episode_artifacts(h5_path, created_videos)
        touched = True

        staged = _stage_videos(meta, options, scratch)
        episodes_dir.mkdir(parents=True, exist_ok=True)
        writer = EpisodeWriter(
            Path(options.samples_root),
            options.session_id,
            episode_id,
            trial_id=str(trial_id),
            task_ids=[options.annotation_policy.attachment_id],
            lab_id=options.lab_id,
            profile=options.profile,
            collection_mode=options.collection_mode,
        )
        command_mode = options.profile.control_mode or "joint_position"
        timestamps = sample_times(n, target_hz)
        try:
            for i in range(n):
                writer.append_proprioception(
                    timestamp=float(timestamps[i]),
                    joint_position=arms_state[i],
                    ee_state=grip_state[i],
                    provenance={"joint_position": prov, "ee_state": prov},
                )
                writer.append_command(
                    timestamp=float(timestamps[i]),
                    target=np.concatenate([arms_action[i], grip_action[i]]),
                    control_mode=command_mode,
                )
            for camera, path in staged.items():
                writer.attach_video(camera, path, move=True)
        finally:
            writer.close()

        annotate_episode(Path(options.samples_root), episode_id, fields, validate_after=False)

        if options.validate:
            _validate(options.samples_root, episode_id)
    except Exception:
        if touched and not options.keep_invalid:
            _remove_episode_artifacts(h5_path, created_videos)
            _delete_trials_row(session_dir / f"trials_{options.session_id}.csv", trial_id)
        raise
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return EpisodeRecord(
        trial_id=trial_id,
        episode_id=episode_id,
        source_index=source_index,
        status="written",
        frames=n,
        duration_s=duration,
        assumed_annotation=annotation.assumed,
    )


def _split_gripper(episode, profile):
    names = list(episode.joint_names)
    if len(names) < 2:
        raise ConversionError(f"episode {episode.meta.index}: expected arm joints plus a gripper")
    arm_names = names[:-1]
    configured = list((profile.manipulator or {}).get("joint_names") or [])
    normalized = [n.lower().replace("-", "_") for n in arm_names]
    configured_norm = [n.lower().replace("-", "_") for n in configured]
    if normalized != configured_norm:
        raise ConversionError(
            f"episode {episode.meta.index}: source arm joints {arm_names} do not match the profile's "
            f"{configured}. Pass a matching --profile or the data will be mislabelled."
        )
    if episode.state.shape[1] != len(names) or episode.action.shape[1] != len(names):
        raise ConversionError(
            f"episode {episode.meta.index}: arrays are {episode.state.shape[1]}-wide but "
            f"{len(names)} joint names were declared"
        )
    return (
        episode.state[:, :-1],
        episode.state[:, -1:],
        episode.action[:, :-1],
        episode.action[:, -1:],
        names,
    )


def _stage_videos(meta, options: ConvertOptions, scratch: Path) -> dict[str, Path]:
    staged: dict[str, Path] = {}
    for camera, segment in meta.videos.items():
        source_media = video_mod.probe_header(segment.path)
        dest = video_mod.stage_path(scratch, camera)
        video_mod.extract_segment(
            segment.path,
            dest,
            start_frame=segment.start_frame,
            frame_count=segment.frame_count,
            fps=segment.fps,
            mode=options.video_mode,
            crf=options.crf,
            preset=options.preset,
            media=source_media,
        )
        if options.video_mode != "copy":
            video_mod.ensure_browser_playable(dest)
        staged[camera] = dest
    return staged


def _validate(samples_root: Path, episode_id: str) -> None:
    from datahive.validate import ValidationError, validate_episode

    try:
        validate_episode(Path(samples_root), episode_id, update_index=False)
    except ValidationError as err:
        problems = getattr(err, "problems", None) or [str(err)]
        raise ConversionError("datahive validate failed: " + " | ".join(problems)) from err


def _remove_episode_artifacts(h5_path: Path, videos: list[Path]) -> None:
    for path in [h5_path, *videos]:
        try:
            Path(path).unlink()
        except FileNotFoundError:
            pass


def _delete_trials_row(csv_path: Path, trial_id: int) -> None:
    try:
        from datahive.trials import delete_row

        delete_row(csv_path, str(trial_id))
    except Exception:  # noqa: BLE001 - best-effort cleanup of a row we just wrote
        pass
