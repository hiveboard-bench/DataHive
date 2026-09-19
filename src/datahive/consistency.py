"""Data-consistency rules applied by `validate`, modelled on oopsie-data's
validator: metadata present, duration bounds, finite/aligned trajectories,
video files real, contained, sized and timed like the episode, and the
annotation agreeing with the recording."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
import numpy as np

from datahive.attachments import load_registry
from datahive.paths import EpisodePaths
from datahive.schema import (
    ANNOTATION_SCHEMA_LEGACY,
    EPISODE_SCHEMA_LEGACY,
    KNOWN_ANNOTATION_SCHEMAS,
    KNOWN_EPISODE_SCHEMAS,
    EpisodeHeader,
)

MIN_DURATION_S = 1.0
MAX_DURATION_S = 600.0
MIN_VIDEO_SIDE_PX = 180
MAX_VIDEO_SIDE_PX = 1280
VIDEO_DURATION_TOL_S = 0.5
VIDEO_FPS_TOL = 0.5
CROSS_CAMERA_FRAME_TOL = 1
TIMEOUT_SLACK_S = 1.0
PLACEHOLDER_LAB_IDS = {"", "your_lab_id"}


def probe_video(path: Path) -> dict[str, Any] | None:
    """{width, height, fps, frames} via OpenCV, or None if OpenCV is missing
    (raises ValueError if the file cannot be decoded)."""
    try:
        import cv2
    except ImportError:
        return None
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("cannot be opened as a video")
        info = {
            "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": float(cap.get(cv2.CAP_PROP_FPS)),
            "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        }
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        info["encoding"] = "".join(chr((fourcc >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ").lower()
    finally:
        cap.release()
    if info["frames"] <= 0 or info["width"] <= 0 or info["height"] <= 0:
        raise ValueError("has no decodable frames")
    return info


def _metadata(header: EpisodeHeader, row: dict | None, problems: list[str], warnings: list[str]) -> None:
    if header.lab_id.strip() in PLACEHOLDER_LAB_IDS:
        problems.append("lab_id is empty or still the 'your_lab_id' placeholder.")
    if not header.episode_id.strip():
        problems.append("episode_id is empty.")
    if header.schema_version not in KNOWN_EPISODE_SCHEMAS:
        problems.append(
            f"Unsupported episode schema '{header.schema_version}'; upgrade datahive-tools."
        )
    elif header.schema_version == EPISODE_SCHEMA_LEGACY:
        warnings.append("Episode file predates schema versioning (datahive_episode_v0).")
    if row is not None:
        if not (row.get("operator_name") or "").strip():
            problems.append("operator_name is required (who ran the trial).")
        if not (row.get("annotator_name") or "").strip():
            problems.append("annotator_name is required (who labelled the episode).")
        version = row.get("schema_version") or ANNOTATION_SCHEMA_LEGACY
        if version not in KNOWN_ANNOTATION_SCHEMAS:
            problems.append(f"Unsupported annotation schema '{version}'; upgrade datahive-tools.")
        elif version == ANNOTATION_SCHEMA_LEGACY:
            warnings.append("Annotation predates schema versioning; save it again to stamp the current schema.")


def _h5_annotation(paths: EpisodePaths, row: dict | None, problems: list[str]) -> None:
    from datahive.annotate import read_h5_annotations

    try:
        stored = read_h5_annotations(paths.h5)
    except Exception as e:
        problems.append(f"Could not read episode_annotations from {paths.h5.name}: {e}")
        return
    for annotator, ann in stored.items():
        if ann.get("schema_version") not in KNOWN_ANNOTATION_SCHEMAS:
            problems.append(
                f"Annotation by '{annotator}' inside the .h5 has unsupported schema "
                f"'{ann.get('schema_version')}'; upgrade datahive-tools."
            )
    if row is None or not stored:
        return
    mine = stored.get((row.get("annotator_name") or "").strip().replace("/", "_"))
    if mine is not None and mine.get("outcome") != row.get("outcome"):
        problems.append(
            "Annotation stored in the .h5 disagrees with trials.csv "
            f"(outcome '{mine.get('outcome')}' vs '{row.get('outcome')}'); save the annotation again."
        )


def _trajectories(h5_path: Path, header: EpisodeHeader, problems: list[str]) -> float | None:
    """Checks finite, aligned, joint-DOF-correct arrays. Returns the episode
    duration in seconds (from proprioception timestamps) if determinable."""
    duration: float | None = None
    lengths: dict[str, int] = {}
    try:
        with h5py.File(h5_path, "r") as f:
            for group_name in ("proprioception", "commands"):
                grp = f.get(group_name)
                if grp is None:
                    problems.append(f"Missing /{group_name} group.")
                    continue
                for name, ds in grp.items():
                    if ds.shape[0] == 0:
                        problems.append(f"/{group_name}/{name} is empty.")
                        continue
                    lengths[f"{group_name}/{name}"] = int(ds.shape[0])
                    if name == "control_mode":
                        continue
                    arr = np.asarray(ds[:])
                    if not np.issubdtype(arr.dtype, np.number):
                        problems.append(f"/{group_name}/{name} is not numeric.")
                    elif np.issubdtype(arr.dtype, np.floating) and not np.isfinite(arr).all():
                        problems.append(f"/{group_name}/{name} contains NaN or infinite values.")
                if group_name == "commands" and header.action_joint_names and "target" in grp:
                    joint_actions = {"joint_position", "joint_velocity"} & set(header.action_space)
                    width = grp["target"].shape[-1] if grp["target"].ndim > 1 else 1
                    if joint_actions and width != len(header.action_joint_names):
                        problems.append(
                            f"/commands/target has {width} values per step but the profile lists "
                            f"{len(header.action_joint_names)} action joint names."
                        )
                if group_name == "proprioception" and "timestamp" in grp and grp["timestamp"].shape[0] >= 2:
                    ts = np.asarray(grp["timestamp"][:]).reshape(-1)
                    duration = float(ts[-1] - ts[0])
                    joints = header.manipulator.get("joint_names") or []
                    jp = grp.get("joint_position")
                    if jp is not None and joints and jp.shape[-1] != len(joints):
                        problems.append(
                            f"joint_position has {jp.shape[-1]} DOF but the header lists "
                            f"{len(joints)} joint names."
                        )
    except Exception as e:
        problems.append(f"Could not inspect trajectories in {h5_path.name}: {e}")
        return duration
    if len(set(lengths.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(lengths.items()))
        problems.append(f"Arrays do not share the same number of steps ({detail}).")
    return duration


def _videos(
    paths: EpisodePaths, header: EpisodeHeader, duration: float | None,
    problems: list[str], warnings: list[str],
) -> None:
    root = paths.samples_root.resolve()
    frame_counts: dict[str, int] = {}
    sizes: dict[str, str] = {}
    rates: dict[str, float] = {}
    probed_any = False
    for cam in header.cameras:
        cname = cam.get("name") or "unnamed"
        fname = cam.get("file")
        if not fname:
            problems.append(f"Camera '{cname}' has no video file recorded.")
            continue
        if Path(fname).is_absolute():
            problems.append(f"Camera '{cname}' video path must be relative, got '{fname}'.")
            continue
        vpath = (paths.h5.parent / fname).resolve()
        if not vpath.is_relative_to(root):
            problems.append(f"Camera '{cname}' video path '{fname}' escapes the samples directory.")
            continue
        if not vpath.is_file():
            problems.append(f"Camera '{cname}' references missing video file {fname}.")
            continue
        if vpath.stat().st_size == 0:
            problems.append(f"Camera '{cname}' video file {fname} is empty.")
            continue
        try:
            info = probe_video(vpath)
        except ValueError as e:
            problems.append(f"Camera '{cname}' video {fname} {e}.")
            continue
        if info is None:
            continue
        probed_any = True
        w, h = info["width"], info["height"]
        if not (MIN_VIDEO_SIDE_PX <= min(w, h) and max(w, h) <= MAX_VIDEO_SIDE_PX):
            problems.append(
                f"Camera '{cname}' video is {w}x{h}; each side must be between "
                f"{MIN_VIDEO_SIDE_PX} and {MAX_VIDEO_SIDE_PX} px."
            )
        declared = str(cam.get("resolution") or "").lower().replace(" ", "")
        if declared and declared != f"{w}x{h}":
            problems.append(f"Camera '{cname}' video is {w}x{h} but the episode header records {declared}.")
        if cam.get("fps") and abs(float(cam["fps"]) - info["fps"]) > VIDEO_FPS_TOL:
            problems.append(
                f"Camera '{cname}' video is {info['fps']:.1f} fps but the episode header records {cam['fps']}."
            )
        frame_counts[cname] = info["frames"]
        sizes[cname] = f"{w}x{h}"
        rates[cname] = info["fps"]
        if duration is not None and info["fps"] > 0:
            vdur = info["frames"] / info["fps"]
            if abs(vdur - duration) > VIDEO_DURATION_TOL_S:
                problems.append(
                    f"Camera '{cname}' video lasts {vdur:.2f}s but the episode lasts {duration:.2f}s "
                    f"(tolerance {VIDEO_DURATION_TOL_S}s)."
                )
    if len(set(sizes.values())) > 1:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(sizes.items()))
        problems.append(f"All cameras must have the same resolution: {detail}.")
    if rates and max(rates.values()) - min(rates.values()) > VIDEO_FPS_TOL:
        detail = ", ".join(f"{k}={v:.1f}" for k, v in sorted(rates.items()))
        problems.append(f"All cameras must have the same fps: {detail}.")
    if len(frame_counts) > 1 and max(frame_counts.values()) - min(frame_counts.values()) > CROSS_CAMERA_FRAME_TOL:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(frame_counts.items()))
        problems.append(f"Cameras must have the same frame count (within {CROSS_CAMERA_FRAME_TOL}): {detail}.")
    if header.cameras and not probed_any and not problems:
        warnings.append("OpenCV is not installed; video content was not checked (pip install 'datahive-tools[video]').")


def _annotation_vs_recording(
    row: dict, duration: float | None, samples_root: Path, problems: list[str], warnings: list[str]
) -> None:
    if duration is None:
        return
    if not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
        problems.append(
            f"Episode lasts {duration:.2f}s; it must be between {MIN_DURATION_S:.0f} and {MAX_DURATION_S:.0f} s."
        )
    outcome = row.get("outcome")
    info = load_registry(samples_root).get(row.get("attachment_id", ""))
    timeout = info.timeout if info else None
    completion = row.get("completion_time_s")

    if completion not in (None, "") and row.get("completion_source") != "timer":
        try:
            if float(completion) > duration + TIMEOUT_SLACK_S:
                problems.append(
                    f"completion_time_s ({float(completion):.1f}s) is longer than the recording ({duration:.1f}s)."
                )
        except ValueError:
            pass
    if timeout:
        if outcome == "success" and duration > timeout + TIMEOUT_SLACK_S:
            problems.append(
                f"Outcome is success but the episode ({duration:.1f}s) exceeds the {timeout}s "
                f"timeout for '{row.get('attachment_id')}'; it should be 'timeout'."
            )
        elif outcome == "timeout" and duration < 0.5 * timeout:
            warnings.append(
                f"Outcome is timeout but the episode ({duration:.1f}s) is far shorter than the {timeout}s limit."
            )


def consistency_problems(
    samples_root: Path, paths: EpisodePaths, header: EpisodeHeader, row: dict | None
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    warnings: list[str] = []
    _metadata(header, row, problems, warnings)
    _h5_annotation(paths, row, problems)
    duration = _trajectories(paths.h5, header, problems)
    _videos(paths, header, duration, problems, warnings)
    if row is not None:
        _annotation_vs_recording(row, duration, samples_root, problems, warnings)
    elif duration is not None and not (MIN_DURATION_S <= duration <= MAX_DURATION_S):
        problems.append(
            f"Episode lasts {duration:.2f}s; it must be between {MIN_DURATION_S:.0f} and {MAX_DURATION_S:.0f} s."
        )
    return problems, warnings


def missing_parts(paths: EpisodePaths, header: EpisodeHeader) -> list[str]:
    """What an episode lacks to be complete: the recorded HDF5 data and one video
    per camera. Empty list means complete."""
    missing: list[str] = []
    try:
        with h5py.File(paths.h5, "r") as f:
            if "proprioception" not in f or "timestamp" not in f["proprioception"]:
                missing.append("HDF5 recording data")
    except OSError:
        missing.append("HDF5 recording data")
    for cam in header.cameras:
        fname = cam.get("file")
        if not fname or not (paths.h5.parent / fname).is_file():
            missing.append(f"video: {cam.get('name') or 'camera'}")
    return missing


def video_duration_s(paths: EpisodePaths, header: EpisodeHeader) -> float | None:
    """Length of the first camera's video in seconds (needs OpenCV), or None."""
    for cam in header.cameras:
        fname = cam.get("file")
        if not fname:
            continue
        try:
            info = probe_video(paths.h5.parent / fname)
        except (ValueError, OSError):
            return None
        if info and info["fps"] > 0:
            return round(info["frames"] / info["fps"], 3)
        return None
    return None
